from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from app.models import GenerationTaskStatus
from app.providers import KlingMalformedResponseError, KlingProvider, KlingProviderContractError
from app.providers.kling_client import KlingJsonResponse
from app.services import GenerationTaskRecord, TaskRegistry, VideoEngine


TASK_ID = "908662318240763933"


def task_data(status="submitted"):
    return {"id": TASK_ID, "status": status, "external_id": "external-safe",
            "create_time": 1781080778802, "update_time": 1781080794151}


def envelope(data):
    return KlingJsonResponse({"code": 0, "message": "ok", "request_id": "request-safe", "data": data},
                             http_status=200)


def video_output():
    return {"type": "video", "id": "video-1", "url": "https://signed.example/video?secret=yes",
            "watermark_url": "https://signed.example/watermark?secret=yes", "duration": "15"}


class KlingSingleQueryResponseTests(unittest.TestCase):
    def test_single_object_submitted_and_processing(self):
        for status, expected in (("submitted", GenerationTaskStatus.SUBMITTED),
                                 ("processing", GenerationTaskStatus.PROCESSING)):
            with self.subTest(status=status):
                task = KlingProvider(client=FakeClient(envelope(task_data(status)))).get_task_by_id(TASK_ID)
                self.assertEqual(task.normalized_status, expected)

    def test_single_object_succeeded_maps_video_and_tolerates_missing_billing(self):
        data = task_data("succeeded"); data["outputs"] = [video_output()]
        task = KlingProvider(client=FakeClient(envelope(data))).get_task_by_id(TASK_ID)
        self.assertEqual(task.normalized_status, GenerationTaskStatus.SUCCEEDED)
        self.assertEqual(task.artifacts[0].artifact_id, "video-1")
        self.assertEqual(task.artifacts[0].duration_seconds, 15.0)
        self.assertNotIn("billing", task.provider_metadata)

    def test_single_object_failed_preserves_documented_message(self):
        data = task_data("failed"); data["message"] = "generation failed"
        task = KlingProvider(client=FakeClient(envelope(data))).get_task_by_id(TASK_ID)
        self.assertEqual(task.normalized_status, GenerationTaskStatus.FAILED)
        self.assertEqual(task.error_message, "generation failed")

    def test_batch_list_remains_compatible(self):
        task = KlingProvider(client=FakeClient(envelope([task_data("processing")]))).get_task_by_id(TASK_ID)
        self.assertEqual(task.normalized_status, GenerationTaskStatus.PROCESSING)

    def test_single_id_mismatch_is_explicit(self):
        data = task_data(); data["id"] = "different-task"
        with self.assertRaises(KlingProviderContractError) as caught:
            KlingProvider(client=FakeClient(envelope(data))).get_task_by_id(TASK_ID)
        self.assertEqual(caught.exception.provider_task_id, "different-task")

    def test_missing_external_id_and_processing_outputs_are_optional(self):
        data = task_data("processing"); del data["external_id"]
        task = KlingProvider(client=FakeClient(envelope(data))).get_task_by_id(TASK_ID)
        self.assertIsNone(task.external_correlation_id)
        self.assertEqual(task.artifacts, [])

    def test_succeeded_requires_outputs_and_preserves_task_id_in_error(self):
        with self.assertRaises(KlingMalformedResponseError) as caught:
            KlingProvider(client=FakeClient(envelope(task_data("succeeded")))).get_task_by_id(TASK_ID)
        self.assertEqual(caught.exception.provider_task_id, TASK_ID)
        self.assertEqual(caught.exception.http_status, 200)

    def test_unknown_status_is_contract_error_with_task_id(self):
        with self.assertRaises(KlingProviderContractError) as caught:
            KlingProvider(client=FakeClient(envelope(task_data("queued")))).get_task_by_id(TASK_ID)
        self.assertEqual(caught.exception.provider_task_id, TASK_ID)

    def test_signed_urls_are_not_persisted_by_polling(self):
        data = task_data("succeeded"); data["outputs"] = [video_output()]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); registry = TaskRegistry(root / "tasks"); now = datetime.now(timezone.utc)
            registry.create(GenerationTaskRecord(provider="kling", provider_task_id=TASK_ID,
                normalized_status=GenerationTaskStatus.PROCESSING, created_at=now, updated_at=now))
            engine = VideoEngine({"kling": KlingProvider(client=FakeClient(envelope(data)))}, registry, object())
            refreshed = engine.refresh(TASK_ID)
            manifest = (root / "tasks" / f"{TASK_ID}.json").read_text(encoding="utf-8")
            self.assertEqual(refreshed.normalized_status, GenerationTaskStatus.SUCCEEDED)
            self.assertNotIn("https://", manifest)
            self.assertNotIn("secret", manifest)

    def test_query_diagnostics_are_allowlisted_and_value_free(self):
        data = task_data("succeeded"); data["outputs"] = None; data["billing"] = [{"secret": "999"}]
        data["prompt"] = "private prompt"; data["url"] = "https://signed.example/private"
        with self.assertRaises(KlingMalformedResponseError) as caught:
            KlingProvider(client=FakeClient(envelope(data))).get_task_by_id(TASK_ID)
        output = "\n".join(caught.exception.response_shape)
        self.assertIn("root: object", output)
        self.assertIn("data: object", output)
        self.assertIn("data.id: string", output)
        for forbidden in ("billing", "prompt", "url", "https://", "999", "private"):
            self.assertNotIn(forbidden, output)


class FakeClient:
    def __init__(self, payload): self.payload = payload; self.get_calls = 0; self.submit_calls = 0
    def get_json(self, path, params): self.get_calls += 1; return self.payload
    def post_json(self, path, payload): self.submit_calls += 1; raise AssertionError("submission forbidden")


if __name__ == "__main__": unittest.main()
