import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.cli.project_resume import _failure
from app.models import GenerationTaskStatus
from app.project import ProjectFailureStage, ProjectRecord, ProjectStatus
from app.providers import KlingHttpClient, KlingMalformedJsonError, KlingMalformedResponseError
from app.providers.kling_dtos import KlingCreateTaskResponse
from app.providers.kling_schema_diagnostics import submit_shape_summary
from app.services import TaskRegistry, TaskRegistryError, VideoEngine, VideoEngineRegistryError, VideoProviderOperationError


def response(data=None, **root):
    return {"code": 0, "message": "ok", "request_id": "request-safe", "data": data, **root}


class KlingSubmitResponseDiagnosticsTests(unittest.TestCase):
    def test_documented_create_response(self):
        parsed = KlingCreateTaskResponse.parse(response({"id": "task-1", "status": "submitted",
            "create_time": 1, "update_time": 2, "external_id": "external-1"}))
        self.assertEqual(parsed.data.id, "task-1")

    def test_root_level_id_response(self):
        parsed = KlingCreateTaskResponse.parse(response({"status": "submitted", "create_time": 1,
            "update_time": 2}, id="root-task"))
        self.assertEqual(parsed.data.id, "root-task")

    def test_data_task_id_response(self):
        parsed = KlingCreateTaskResponse.parse(response({"task_id": "snake-task", "status": "submitted",
            "create_time": 1, "update_time": 2}))
        self.assertEqual(parsed.data.id, "snake-task")

    def test_missing_external_id_is_optional(self):
        parsed = KlingCreateTaskResponse.parse(response({"id": "task-1", "status": "submitted",
            "create_time": 1, "update_time": 2}))
        self.assertIsNone(parsed.data.external_id)

    def test_task_id_is_carried_when_optional_field_is_malformed(self):
        with self.assertRaises(KlingMalformedResponseError) as caught:
            KlingCreateTaskResponse.parse(response({"id": "paid-task", "status": [],
                "create_time": 1, "update_time": 2}))
        self.assertEqual(caught.exception.provider_task_id, "paid-task")
        self.assertEqual(caught.exception.provider_code, 0)

    def test_list_shape_is_bounded_to_first_item_names_and_types(self):
        shape = submit_shape_summary(response([{"id": "secret-value", "billing": "secret-billing"},
                                                {"prompt": "secret-prompt"}]))
        output = "\n".join(shape)
        self.assertIn("data item count: 2", output)
        self.assertIn("data[0].id: string", output)
        self.assertNotIn("secret", output)
        self.assertNotIn("prompt", output)
        self.assertNotIn("billing", output)

    def test_shape_suppresses_raw_body_prompt_credentials_and_values(self):
        payload = response({"id": "task-value", "status": "submitted", "create_time": 1,
            "update_time": 2, "external_id": "external-value", "prompt": "private prompt"},
            Authorization="Bearer credential", billing=["private"])
        output = "\n".join(submit_shape_summary(payload))
        for forbidden in ("task-value", "private prompt", "credential", "Authorization", "billing"):
            self.assertNotIn(forbidden, output)

    def test_malformed_json_has_success_http_status_without_body(self):
        client = KlingHttpClient(api_key="credential", opener=lambda *_args, **_kwargs: FakeResponse(b"not-json"))
        with self.assertRaises(KlingMalformedJsonError) as caught:
            client.post_json("/tasks", {"prompt": "private prompt"})
        self.assertEqual(caught.exception.http_status, 200)
        self.assertNotIn("not-json", str(caught.exception))
        self.assertNotIn("private prompt", str(caught.exception))

    def test_engine_adopts_task_before_propagating_parse_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = TaskRegistry(Path(directory) / "tasks")
            error = KlingMalformedResponseError("safe", provider_task_id="paid-task",
                external_correlation_id="external-safe", response_shape=("root: object",))
            provider = Mock(); provider.submit_generation.side_effect = error
            engine = VideoEngine({"kling": provider}, registry, Mock())
            with self.assertRaises(VideoProviderOperationError) as caught:
                engine.submit(Mock(), provider="kling")
            adopted = registry.load("paid-task")
            self.assertEqual(adopted.external_correlation_id, "external-safe")
            self.assertEqual(caught.exception.provider_task_id, "paid-task")

    def test_registry_failure_after_extraction_is_distinct(self):
        registry = Mock(); registry.exists.return_value = False
        registry.create.side_effect = TaskRegistryError("unsafe detail")
        provider = Mock(); provider.submit_generation.side_effect = KlingMalformedResponseError(
            "safe", provider_task_id="paid-task")
        with self.assertRaises(VideoEngineRegistryError) as caught:
            VideoEngine({"kling": provider}, registry, Mock()).submit(Mock(), provider="kling")
        self.assertEqual(caught.exception.provider_task_id, "paid-task")

    def test_project_failure_output_contains_only_safe_submit_metadata(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        record = ProjectRecord(project_id="counting", episode_id="episode", status=ProjectStatus.FAILED,
            video_production_id="counting-video", lyrics_path=Path("lyrics.json"), music_directory=Path("music"),
            video_directory=Path("video"), final_directory=Path("final"),
            failure_stage=ProjectFailureStage.VIDEO_SUBMISSION, failure_category="video_submission_failed",
            safe_message="Scene scene-0001 submit response could not be parsed.", failed_scene_id="scene-0001",
            submit_http_status=200, submit_provider_code=0, submit_provider_task_id="paid-task",
            submit_response_shape=("root: object", "data: object", "data.id: string"), created_at=now, updated_at=now)
        with patch("builtins.print") as emit:
            _failure(record)
        output = "\n".join(call.args[0] for call in emit.call_args_list)
        self.assertIn("Provider task ID: paid-task", output)
        self.assertIn("HTTP status: 200", output)
        self.assertNotIn("prompt", output)


class FakeResponse:
    status = 200
    headers = {}
    def __init__(self, body): self.body = body
    def getcode(self): return self.status
    def read(self): return self.body
    def close(self): pass


if __name__ == "__main__":
    unittest.main()
