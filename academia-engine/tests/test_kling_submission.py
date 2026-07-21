from copy import deepcopy
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from app.models import (
    Camera,
    CharacterAction,
    GenerationTaskStatus,
    Transition,
    VideoEnvironment,
    VideoGenerationRequest,
    VideoRequest,
)
from app.config import KlingGenerationConfigurationError, KlingGenerationSettings
from app.providers import (
    KlingCreateTaskResponse,
    KlingMalformedResponseError,
    KlingProvider,
    KlingProviderApiError,
    KlingProviderContractError,
    KlingTextToVideoMapper,
    KlingUnsupportedConfigurationError,
)
from app.providers.kling_client import KlingJsonResponse


# Official Create Task response shape supplied for Sprint 6.3, populated with a
# documented supported status so the provider-status mapping can be tested.
OFFICIAL_CREATE_TASK_SUCCESS_FIXTURE = {
    "code": 0,
    "message": "string",
    "request_id": "string",
    "data": {
        "id": "string",
        "status": "submitted",
        "create_time": 1781080778802,
        "update_time": 1781080794151,
        "external_id": "string",
    },
}


class KlingSubmissionTests(unittest.TestCase):
    def test_mapper_serializes_only_the_documented_request_shape(self) -> None:
        payload = KlingTextToVideoMapper(KlingGenerationSettings()).map(
            self._request(), external_task_id="smoke-correlation-01"
        ).to_payload()

        self.assertEqual(
            payload,
            {
                "prompt": (
                    "Scene 1. Environment: sunny garden; a flower garden for a preschool animation; "
                    "time of day: morning; lighting: soft daylight. Characters: No characters on screen. "
                    "Actions: ladybug: waves gently, emotion: happy. Camera: wide shot, eye_level angle, "
                    "static movement. Transition: fade."
                ),
                "settings": {
                    "resolution": "720p",
                    "aspect_ratio": "16:9",
                    "duration": 15,
                    "audio": "off",
                    "multi_shot": True,
                },
                "options": {
                    "external_task_id": "smoke-correlation-01",
                    "watermark_info": {"enabled": False},
                },
            },
        )

    def test_mapper_rejects_unconfirmed_duration(self) -> None:
        with self.assertRaises(KlingUnsupportedConfigurationError):
            KlingTextToVideoMapper(KlingGenerationSettings()).map(
                self._request(duration_seconds=3), external_task_id="smoke-correlation-03"
            )

    def test_configuration_uses_720p_when_resolution_is_absent(self) -> None:
        settings = KlingGenerationSettings.from_environment({})

        self.assertEqual(settings.resolution, "720p")

    def test_configuration_accepts_720p_resolution(self) -> None:
        settings = KlingGenerationSettings.from_environment({"KLING_RESOLUTION": "720p"})

        self.assertEqual(settings.resolution, "720p")

    def test_configuration_normalizes_resolution_whitespace_only(self) -> None:
        settings = KlingGenerationSettings.from_environment({"KLING_RESOLUTION": " 720p "})

        self.assertEqual(settings.resolution, "720p")

    def test_configuration_rejects_unsupported_resolution_before_http(self) -> None:
        client = FakeKlingClient(OFFICIAL_CREATE_TASK_SUCCESS_FIXTURE)

        with patch.dict("os.environ", {"KLING_RESOLUTION": "720P"}, clear=False), self.assertRaises(
            KlingGenerationConfigurationError
        ):
            KlingProvider(client=client)

        self.assertEqual(client.path, "")

    def test_configuration_rejects_unsupported_resolution(self) -> None:
        with self.assertRaises(KlingGenerationConfigurationError):
            KlingGenerationSettings.from_environment({"KLING_RESOLUTION": "720P"})

    def test_mapper_uses_injected_generation_settings(self) -> None:
        settings = KlingGenerationSettings.from_environment(
            {
                "KLING_RESOLUTION": "720p",
                "KLING_DURATION": "15",
                "KLING_AUDIO": "off",
                "KLING_MULTI_SHOT": "true",
            }
        )

        payload = KlingTextToVideoMapper(settings).map(
            self._request(), external_task_id="configured-settings"
        ).to_payload()

        self.assertEqual(
            payload["settings"],
            {
                "resolution": "720p",
                "aspect_ratio": "16:9",
                "duration": 15,
                "audio": "off",
                "multi_shot": True,
            },
        )

    def test_submit_maps_the_official_response_fields(self) -> None:
        client = FakeKlingClient(OFFICIAL_CREATE_TASK_SUCCESS_FIXTURE)

        task = KlingProvider(client=client).submit_scene(self._request())

        self.assertEqual(client.path, "/text-to-video/kling-3.0")
        self.assertEqual(task.request_id, "scene-01")
        self.assertEqual(task.external_task_id, "string")
        self.assertEqual(task.provider_status, "submitted")
        self.assertEqual(task.normalized_status, GenerationTaskStatus.SUBMITTED)
        self.assertEqual(task.provider_request_id, "string")
        self.assertEqual(task.provider_code, 0)
        self.assertEqual(task.provider_message, "string")
        self.assertEqual(task.external_correlation_id, "string")
        self.assertEqual(
            task.submitted_at, datetime.fromtimestamp(1781080778802 / 1000, tz=timezone.utc)
        )
        self.assertEqual(
            task.updated_at, datetime.fromtimestamp(1781080794151 / 1000, tz=timezone.utc)
        )

    def test_submit_accepts_exact_observed_minimal_success_response(self) -> None:
        fixture = KlingJsonResponse({
            "code": 0,
            "data": {
                "id": "908664449932857438",
                "status": "submitted",
                "external_id": "scene-3-correlation",
                "create_time": 1781080778802,
                "update_time": 1781080794151,
            },
        }, http_status=200)

        task = KlingProvider(client=FakeKlingClient(fixture)).submit_scene(self._request())

        self.assertEqual(task.external_task_id, "908664449932857438")
        self.assertEqual(task.normalized_status, GenerationTaskStatus.SUBMITTED)
        self.assertIsNone(task.provider_request_id)
        self.assertIsNone(task.provider_message)

    def test_submit_accepts_only_required_identity_and_status_fields(self) -> None:
        fixture = KlingJsonResponse({"code": 0, "data": {
            "id": "minimal-task", "status": "processing",
        }}, http_status=200)

        task = KlingProvider(client=FakeKlingClient(fixture)).submit_scene(self._request())

        self.assertEqual(task.external_task_id, "minimal-task")
        self.assertEqual(task.normalized_status, GenerationTaskStatus.PROCESSING)
        self.assertIsNone(task.external_correlation_id)
        self.assertIsNone(task.submitted_at)
        self.assertIsNone(task.updated_at)

    def test_submit_maps_every_documented_status(self) -> None:
        expected_statuses = {
            "submitted": GenerationTaskStatus.SUBMITTED,
            "processing": GenerationTaskStatus.PROCESSING,
            "succeeded": GenerationTaskStatus.SUCCEEDED,
            "failed": GenerationTaskStatus.FAILED,
        }
        for provider_status, normalized_status in expected_statuses.items():
            with self.subTest(provider_status=provider_status):
                fixture = deepcopy(OFFICIAL_CREATE_TASK_SUCCESS_FIXTURE)
                fixture["data"]["status"] = provider_status
                task = KlingProvider(client=FakeKlingClient(fixture)).submit_scene(self._request())
                self.assertEqual(task.normalized_status, normalized_status)
                self.assertEqual(
                    task.completed_at is not None,
                    normalized_status in {GenerationTaskStatus.SUCCEEDED, GenerationTaskStatus.FAILED},
                )

    def test_nonzero_code_is_a_provider_error(self) -> None:
        fixture = deepcopy(OFFICIAL_CREATE_TASK_SUCCESS_FIXTURE)
        fixture["code"] = 1

        with self.assertRaises(KlingProviderApiError):
            KlingProvider(client=FakeKlingClient(fixture)).submit_scene(self._request())

    def test_missing_data_is_malformed(self) -> None:
        fixture = deepcopy(OFFICIAL_CREATE_TASK_SUCCESS_FIXTURE)
        del fixture["data"]

        with self.assertRaises(KlingMalformedResponseError):
            KlingCreateTaskResponse.parse(fixture)

    def test_missing_data_id_is_malformed(self) -> None:
        fixture = deepcopy(OFFICIAL_CREATE_TASK_SUCCESS_FIXTURE)
        del fixture["data"]["id"]

        with self.assertRaises(KlingMalformedResponseError):
            KlingCreateTaskResponse.parse(fixture)

    def test_unknown_status_is_a_provider_contract_error(self) -> None:
        fixture = deepcopy(OFFICIAL_CREATE_TASK_SUCCESS_FIXTURE)
        fixture["data"]["status"] = "queued"

        with self.assertRaises(KlingProviderContractError):
            KlingProvider(client=FakeKlingClient(fixture)).submit_scene(self._request())

    def test_invalid_timestamp_is_malformed(self) -> None:
        fixture = deepcopy(OFFICIAL_CREATE_TASK_SUCCESS_FIXTURE)
        fixture["data"]["create_time"] = "not-a-millisecond-timestamp"

        with self.assertRaises(KlingMalformedResponseError):
            KlingProvider(client=FakeKlingClient(fixture)).submit_scene(self._request())

    @staticmethod
    def _request(duration_seconds: int = 15) -> VideoGenerationRequest:
        return VideoGenerationRequest(
            request_id="scene-01",
            video_request=VideoRequest(
                scene_number=1,
                duration_seconds=duration_seconds,
                environment=VideoEnvironment(
                    location_name="sunny garden",
                    location_description="a flower garden for a preschool animation",
                    time_of_day="morning",
                    lighting_description="soft daylight",
                    lighting_intensity="medium",
                ),
                character_actions=[
                    CharacterAction(
                        character_id="ladybug", action="waves gently", emotion="happy"
                    )
                ],
                camera=Camera(shot_type="wide", description="opening view"),
                transition=Transition(type="fade"),
            ),
        )


class FakeKlingClient:
    def __init__(self, response: dict[str, object]) -> None:
        self._response = response
        self.path = ""
        self.payload: dict[str, object] = {}

    def post_json(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        self.path = path
        self.payload = payload
        return self._response

    def get_account_usage(self) -> dict[str, object]:
        return {"status": "ok"}
