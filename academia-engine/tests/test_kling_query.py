from copy import deepcopy
import unittest

from app.models import GenerationTaskStatus
from app.providers import (
    KlingMalformedResponseError,
    KlingProvider,
    KlingProviderApiError,
    KlingProviderContractError,
    KlingTaskNotFoundError,
)


OFFICIAL_QUERY_TASK_FIXTURE = {
    "code": 0,
    "message": "string",
    "request_id": "query-request-01",
    "data": [
        {
            "id": "kling-task-01",
            "status": "submitted",
            "message": "string",
            "create_time": 1781080778802,
            "update_time": 1781080794151,
            "external_id": "external-01",
            "outputs": [],
            "billing": [],
        }
    ],
}


class KlingQueryTests(unittest.TestCase):
    def test_query_uses_documented_external_id_parameter(self) -> None:
        client = FakeKlingClient(OFFICIAL_QUERY_TASK_FIXTURE)

        KlingProvider(client=client).get_task_by_external_id("external-01")

        self.assertEqual(client.path, "/tasks")
        self.assertEqual(client.params, {"external_task_ids": "external-01"})

    def test_no_exact_match_raises_not_found(self) -> None:
        fixture = deepcopy(OFFICIAL_QUERY_TASK_FIXTURE)
        fixture["data"][0]["external_id"] = "different"

        with self.assertRaises(KlingTaskNotFoundError):
            KlingProvider(client=FakeKlingClient(fixture)).get_task_by_external_id("external-01")

    def test_nonzero_query_code_is_a_provider_error(self) -> None:
        fixture = deepcopy(OFFICIAL_QUERY_TASK_FIXTURE)
        fixture["code"] = 1

        with self.assertRaises(KlingProviderApiError):
            KlingProvider(client=FakeKlingClient(fixture)).get_task_by_external_id("external-01")

    def test_unrelated_results_are_ignored_when_an_exact_match_exists(self) -> None:
        fixture = deepcopy(OFFICIAL_QUERY_TASK_FIXTURE)
        unrelated = deepcopy(fixture["data"][0])
        unrelated["id"] = "other-task"
        unrelated["external_id"] = "different"
        fixture["data"].append(unrelated)

        task = KlingProvider(client=FakeKlingClient(fixture)).get_task_by_external_id("external-01")

        self.assertEqual(task.external_task_id, "kling-task-01")

    def test_duplicate_exact_matches_raise_contract_error(self) -> None:
        fixture = deepcopy(OFFICIAL_QUERY_TASK_FIXTURE)
        duplicate = deepcopy(fixture["data"][0])
        duplicate["id"] = "duplicate-task"
        fixture["data"].append(duplicate)

        with self.assertRaises(KlingProviderContractError):
            KlingProvider(client=FakeKlingClient(fixture)).get_task_by_external_id("external-01")

    def test_submitted_and_processing_statuses_are_mapped(self) -> None:
        for provider_status, normalized_status in {
            "submitted": GenerationTaskStatus.SUBMITTED,
            "processing": GenerationTaskStatus.PROCESSING,
        }.items():
            with self.subTest(provider_status=provider_status):
                fixture = deepcopy(OFFICIAL_QUERY_TASK_FIXTURE)
                fixture["data"][0]["status"] = provider_status
                task = KlingProvider(client=FakeKlingClient(fixture)).get_task_by_external_id("external-01")
                self.assertEqual(task.normalized_status, normalized_status)

    def test_failed_task_preserves_its_message(self) -> None:
        fixture = deepcopy(OFFICIAL_QUERY_TASK_FIXTURE)
        fixture["data"][0]["status"] = "failed"
        fixture["data"][0]["message"] = "generation failed"

        task = KlingProvider(client=FakeKlingClient(fixture)).get_task_by_external_id("external-01")

        self.assertEqual(task.normalized_status, GenerationTaskStatus.FAILED)
        self.assertEqual(task.error_message, "generation failed")

    def test_succeeded_task_maps_one_video_artifact(self) -> None:
        fixture = self._succeeded_fixture()
        fixture["data"][0]["outputs"] = [self._video_output("video-01", "12.5")]

        task = KlingProvider(client=FakeKlingClient(fixture)).get_task_by_external_id("external-01")

        self.assertEqual(task.normalized_status, GenerationTaskStatus.SUCCEEDED)
        self.assertEqual(task.artifacts[0].artifact_id, "video-01")
        self.assertEqual(task.artifacts[0].duration_seconds, 12.5)
        self.assertEqual(task.artifacts[0].watermark_url, "https://example.test/watermark.mp4")

    def test_succeeded_video_without_watermark_url_is_accepted(self) -> None:
        fixture = self._succeeded_fixture()
        output = self._video_output("video-01", "12.5")
        del output["watermark_url"]
        fixture["data"][0]["outputs"] = [output]

        task = KlingProvider(client=FakeKlingClient(fixture)).get_task_by_external_id("external-01")

        self.assertEqual(task.artifacts[0].url, "https://example.test/video.mp4")
        self.assertIsNone(task.artifacts[0].watermark_url)

    def test_succeeded_video_with_null_watermark_url_is_accepted(self) -> None:
        fixture = self._succeeded_fixture()
        output = self._video_output("video-01", "12.5")
        output["watermark_url"] = None
        fixture["data"][0]["outputs"] = [output]

        task = KlingProvider(client=FakeKlingClient(fixture)).get_task_by_external_id("external-01")

        self.assertIsNone(task.artifacts[0].watermark_url)

    def test_missing_video_id_still_raises(self) -> None:
        fixture = self._succeeded_fixture()
        output = self._video_output("video-01", "1")
        del output["id"]
        fixture["data"][0]["outputs"] = [output]

        with self.assertRaises(KlingMalformedResponseError):
            KlingProvider(client=FakeKlingClient(fixture)).get_task_by_external_id("external-01")

    def test_unknown_video_output_fields_remain_rejected(self) -> None:
        fixture = self._succeeded_fixture()
        output = self._video_output("video-01", "1")
        output["unknown_field"] = "not allowed"
        fixture["data"][0]["outputs"] = [output]

        with self.assertRaises(KlingMalformedResponseError):
            KlingProvider(client=FakeKlingClient(fixture)).get_task_by_external_id("external-01")

    def test_succeeded_task_maps_multiple_video_artifacts(self) -> None:
        fixture = self._succeeded_fixture()
        fixture["data"][0]["outputs"] = [
            self._video_output("video-01", "12"),
            self._video_output("video-02", "13"),
        ]

        task = KlingProvider(client=FakeKlingClient(fixture)).get_task_by_external_id("external-01")

        self.assertEqual([artifact.artifact_id for artifact in task.artifacts], ["video-01", "video-02"])

    def test_non_video_outputs_are_preserved_without_artifact_mapping(self) -> None:
        fixture = self._succeeded_fixture()
        non_video_output = {"type": "image", "id": "image-01", "url": "https://example.test/image.png"}
        fixture["data"][0]["outputs"] = [self._video_output("video-01", "1"), non_video_output]
        fixture["data"][0]["billing"] = [{"item": "raw-billing-entry"}]

        task = KlingProvider(client=FakeKlingClient(fixture)).get_task_by_external_id("external-01")

        self.assertEqual(len(task.artifacts), 1)
        self.assertEqual(task.provider_metadata["non_video_outputs"], [{"type": "image", "id": "image-01"}])
        self.assertNotIn("billing", task.provider_metadata)

    def test_malformed_video_output_raises(self) -> None:
        fixture = self._succeeded_fixture()
        malformed = self._video_output("video-01", "1")
        del malformed["url"]
        fixture["data"][0]["outputs"] = [malformed]

        with self.assertRaises(KlingMalformedResponseError):
            KlingProvider(client=FakeKlingClient(fixture)).get_task_by_external_id("external-01")

    def test_invalid_video_duration_raises(self) -> None:
        fixture = self._succeeded_fixture()
        fixture["data"][0]["outputs"] = [self._video_output("video-01", "not-a-duration")]

        with self.assertRaises(KlingMalformedResponseError):
            KlingProvider(client=FakeKlingClient(fixture)).get_task_by_external_id("external-01")

    def test_unknown_task_status_raises(self) -> None:
        fixture = deepcopy(OFFICIAL_QUERY_TASK_FIXTURE)
        fixture["data"][0]["status"] = "queued"

        with self.assertRaises(KlingProviderContractError):
            KlingProvider(client=FakeKlingClient(fixture)).get_task_by_external_id("external-01")

    def test_query_by_id_uses_only_documented_task_ids_parameter(self) -> None:
        client = FakeKlingClient(OFFICIAL_QUERY_TASK_FIXTURE)

        task = KlingProvider(client=client).get_task_by_id("kling-task-01")

        self.assertEqual(task.external_task_id, "kling-task-01")
        self.assertEqual(client.path, "/tasks")
        self.assertEqual(client.params, {"task_ids": "kling-task-01"})
        self.assertNotIn("external_task_ids", client.params)

    def test_query_by_id_zero_exact_matches_raises_not_found(self) -> None:
        with self.assertRaises(KlingProviderContractError):
            KlingProvider(client=FakeKlingClient(OFFICIAL_QUERY_TASK_FIXTURE)).get_task_by_id(
                "different-task"
            )

    def test_query_by_id_ignores_unrelated_results(self) -> None:
        fixture = deepcopy(OFFICIAL_QUERY_TASK_FIXTURE)
        unrelated = deepcopy(fixture["data"][0])
        unrelated["id"] = "other-task"
        fixture["data"].append(unrelated)

        task = KlingProvider(client=FakeKlingClient(fixture)).get_task_by_id("kling-task-01")

        self.assertEqual(task.external_task_id, "kling-task-01")

    def test_query_by_id_duplicate_exact_matches_raise_contract_error(self) -> None:
        fixture = deepcopy(OFFICIAL_QUERY_TASK_FIXTURE)
        fixture["data"].append(deepcopy(fixture["data"][0]))

        with self.assertRaises(KlingProviderContractError):
            KlingProvider(client=FakeKlingClient(fixture)).get_task_by_id("kling-task-01")

    def test_query_by_id_maps_submitted_and_processing(self) -> None:
        for provider_status, normalized_status in {
            "submitted": GenerationTaskStatus.SUBMITTED,
            "processing": GenerationTaskStatus.PROCESSING,
        }.items():
            with self.subTest(provider_status=provider_status):
                fixture = deepcopy(OFFICIAL_QUERY_TASK_FIXTURE)
                fixture["data"][0]["status"] = provider_status
                task = KlingProvider(client=FakeKlingClient(fixture)).get_task_by_id("kling-task-01")
                self.assertEqual(task.normalized_status, normalized_status)

    def test_query_by_id_maps_succeeded_video_and_failed_message(self) -> None:
        succeeded = self._succeeded_fixture()
        succeeded["data"][0]["outputs"] = [self._video_output("video-01", "10")]
        succeeded_task = KlingProvider(client=FakeKlingClient(succeeded)).get_task_by_id("kling-task-01")
        self.assertEqual(succeeded_task.artifacts[0].artifact_id, "video-01")

        failed = deepcopy(OFFICIAL_QUERY_TASK_FIXTURE)
        failed["data"][0]["status"] = "failed"
        failed["data"][0]["message"] = "generation failed"
        failed_task = KlingProvider(client=FakeKlingClient(failed)).get_task_by_id("kling-task-01")
        self.assertEqual(failed_task.error_message, "generation failed")

    @staticmethod
    def _succeeded_fixture() -> dict[str, object]:
        fixture = deepcopy(OFFICIAL_QUERY_TASK_FIXTURE)
        fixture["data"][0]["status"] = "succeeded"
        return fixture

    @staticmethod
    def _video_output(video_id: str, duration: str) -> dict[str, str]:
        return {
            "type": "video",
            "id": video_id,
            "url": "https://example.test/video.mp4",
            "watermark_url": "https://example.test/watermark.mp4",
            "duration": duration,
        }


class FakeKlingClient:
    def __init__(self, response: dict[str, object]) -> None:
        self._response = response
        self.path = ""
        self.params: dict[str, str] = {}

    def get_json(self, path: str, params: dict[str, str]) -> dict[str, object]:
        self.path = path
        self.params = params
        return self._response

    def get_account_usage(self) -> dict[str, object]:
        return {"status": "ok"}
