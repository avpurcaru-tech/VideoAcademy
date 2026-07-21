import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from pydantic import ValidationError

from app.config import KlingGenerationSettings
from app.models import (
    GenerationTask,
    GenerationTaskStatus,
    VideoArtifact,
    VideoGenerationRequest,
    VideoGenerationResult,
)

from .kling_client import (
    KlingAuthenticationProbeUnavailableError,
    KlingClientError,
    KlingHttpClient,
)
from .kling_dtos import (
    KlingCreateTaskResponse,
    KlingCreateTaskData,
    KlingMalformedResponseError,
    KlingProviderContractError,
    KlingQueryTasksResponse,
    KlingTaskData,
    KlingTaskNotFoundError,
    KlingVideoOutput,
)
from .kling_mapper import KlingTextToVideoMapper
from .kling_schema_diagnostics import query_shape_summary, shape_summary, validation_details


class KlingSubmissionDisabledError(KlingClientError):
    """Raised until Kling publishes a documented create-task response schema."""


logger = logging.getLogger(__name__)
_RETENTION_NOTE = "Kling generated result URLs may be cleared after 30 days; download promptly."


class KlingProvider:
    """Kling provider with authentication connectivity support."""

    def __init__(
        self,
        client: KlingHttpClient | None = None,
        mapper: KlingTextToVideoMapper | None = None,
        generation_settings: KlingGenerationSettings | None = None,
    ) -> None:
        base_url = os.environ.get("KLING_BASE_URL", "https://api-singapore.klingai.com")
        self._client = client or KlingHttpClient(base_url=base_url)
        self._mapper = mapper or KlingTextToVideoMapper(
            generation_settings or KlingGenerationSettings.from_environment()
        )

    def validate_authentication(self) -> dict[str, Any]:
        raise KlingAuthenticationProbeUnavailableError(
            "Standalone Kling authentication probing is unavailable until a current official endpoint is configured."
        )

    def health_check(self) -> dict[str, Any]:
        """Backward-compatible alias for validate_authentication."""
        return self.validate_authentication()

    def generate_scene(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        raise NotImplementedError(
            "Kling video generation is not implemented; only authentication validation is available."
        )

    def submit_scene(self, request: VideoGenerationRequest) -> GenerationTask:
        """Backward-compatible alias for submit_generation."""
        return self.submit_generation(request)

    def submit_generation(self, request: VideoGenerationRequest) -> GenerationTask:
        correlation_id = uuid4().hex
        provider_request = self._mapper.map(request, external_task_id=correlation_id)
        payload = self._client.post_json("/text-to-video/kling-3.0", provider_request.to_payload())
        response = KlingCreateTaskResponse.parse(payload)
        data = response.data
        if data is None:
            raise KlingMalformedResponseError("Kling Create Task success response is missing data.")
        try:
            return self._to_generation_task(task_data=data, provider_request_id=response.request_id,
                provider_code=response.code, provider_message=response.message,
                internal_request_id=request.request_id, tolerate_optional_metadata=True)
        except (KlingMalformedResponseError, KlingProviderContractError) as error:
            error.provider_task_id = data.id
            error.external_correlation_id = data.external_id
            error.http_status = getattr(payload, "http_status", None)
            error.provider_code = response.code
            if not getattr(error, "response_shape", ()):
                from .kling_schema_diagnostics import submit_shape_summary
                error.response_shape = submit_shape_summary(payload)
            raise

    def get_task(self, external_task_id: str) -> GenerationTask:
        raise NotImplementedError("Kling task retrieval is not implemented yet.")

    def get_task_by_external_id(self, external_id: str) -> GenerationTask:
        return self._get_single_task(
            query_parameter="external_task_ids",
            requested_value=external_id,
            identifier_name="external ID",
            matches=lambda task: task.external_id == external_id,
        )

    def get_task_by_id(self, task_id: str) -> GenerationTask:
        return self._get_single_task(
            query_parameter="task_ids",
            requested_value=task_id,
            identifier_name="task ID",
            matches=lambda task: task.id == task_id,
        )

    def _get_single_task(
        self,
        query_parameter: str,
        requested_value: str,
        identifier_name: str,
        matches: Callable[[KlingTaskData], bool],
    ) -> GenerationTask:
        payload = self._client.get_json("/tasks", params={query_parameter: requested_value})
        response = KlingQueryTasksResponse.parse(payload)
        exact_matches = [task for task in response.data if matches(task)]
        if not exact_matches:
            if identifier_name == "task ID" and len(response.data) == 1:
                error = KlingProviderContractError("Kling Query Task returned a different task ID.")
                error.provider_task_id = response.data[0].id
                error.requested_task_id = requested_value
                error.http_status = getattr(payload, "http_status", None)
                error.provider_code = response.code
                error.response_shape = query_shape_summary(payload)
                raise error
            raise KlingTaskNotFoundError(
                f"Kling task was not found for {identifier_name} {requested_value!r}."
            )
        if len(exact_matches) > 1:
            raise KlingProviderContractError(
                f"Kling Query Task returned multiple tasks for {identifier_name} {requested_value!r}."
            )
        data = exact_matches[0]
        try:
            return self._to_generation_task(task_data=data, provider_request_id=response.request_id,
                provider_code=response.code, provider_message=response.message, internal_request_id=None)
        except (KlingMalformedResponseError, KlingProviderContractError) as error:
            error.provider_task_id = data.id
            error.http_status = getattr(payload, "http_status", None)
            error.provider_code = response.code
            error.response_shape = query_shape_summary(payload)
            raise

    def _to_generation_task(
        self,
        task_data: KlingCreateTaskData | KlingTaskData,
        provider_request_id: str | None,
        provider_code: int,
        provider_message: str | None,
        internal_request_id: str | None,
        tolerate_optional_metadata: bool = False,
    ) -> GenerationTask:
        data = task_data
        normalized_status = self._map_status(data.status)
        created_at = self._optional_timestamp(data.create_time, "data.create_time", tolerate_optional_metadata)
        updated_at = self._optional_timestamp(data.update_time, "data.update_time", tolerate_optional_metadata)
        artifacts, non_video_outputs, billing = self._artifacts_and_metadata(data)
        completed_at = (
            updated_at
            if normalized_status in {GenerationTaskStatus.SUCCEEDED, GenerationTaskStatus.FAILED}
            else None
        )
        return GenerationTask(
            request_id=internal_request_id,
            external_task_id=data.id,
            provider_name="kling",
            provider_status=data.status,
            normalized_status=normalized_status,
            provider_request_id=provider_request_id,
            provider_code=provider_code,
            provider_message=provider_message,
            external_correlation_id=data.external_id,
            error_message=(
                (getattr(data, "message", None) or provider_message)
                if normalized_status == GenerationTaskStatus.FAILED
                else None
            ),
            artifacts=artifacts,
            provider_metadata={
                "kling_request_id": provider_request_id,
                "kling_task_id": data.id,
                "external_id": data.external_id,
                "task_message": getattr(data, "message", None) or provider_message,
                "update_time": data.update_time,
                "non_video_outputs": non_video_outputs,
                "retention_note": _RETENTION_NOTE,
            },
            submitted_at=created_at,
            updated_at=updated_at,
            completed_at=completed_at,
        )

    @staticmethod
    def _artifacts_and_metadata(
        data: KlingCreateTaskData | KlingTaskData,
    ) -> tuple[list[VideoArtifact], list[dict[str, object]], list[dict[str, object]]]:
        outputs = getattr(data, "outputs", None) or []
        billing = getattr(data, "billing", None) or []
        artifacts: list[VideoArtifact] = []
        non_video_outputs: list[dict[str, object]] = []
        for output_index, output in enumerate(outputs):
            if output.get("type") != "video":
                logger.debug("Kling non-video output preserved without video artifact mapping.")
                non_video_outputs.append({key: output[key] for key in ("type", "id") if key in output})
                continue
            try:
                video_output = KlingVideoOutput.model_validate(output)
                duration_seconds = video_output.duration_seconds()
            except ValidationError as error:
                raise KlingMalformedResponseError(
                    "Kling Query Task response contains a malformed video output.",
                    validation_errors=validation_details(
                        error,
                        location_prefix=("data", 0, "outputs", output_index),
                    ),
                    response_shape=shape_summary(
                        output,
                        root_path=f"data.0.outputs.{output_index}",
                    ),
                ) from error
            except KlingMalformedResponseError:
                raise
            artifacts.append(
                VideoArtifact(
                    artifact_id=video_output.id,
                    url=video_output.url,
                    watermark_url=video_output.watermark_url,
                    duration_seconds=duration_seconds,
                    content_type="video",
                )
            )
        return artifacts, non_video_outputs, billing

    @staticmethod
    def _map_status(provider_status: str) -> GenerationTaskStatus:
        status_mapping = {
            "submitted": GenerationTaskStatus.SUBMITTED,
            "processing": GenerationTaskStatus.PROCESSING,
            "succeeded": GenerationTaskStatus.SUCCEEDED,
            "failed": GenerationTaskStatus.FAILED,
        }
        try:
            return status_mapping[provider_status]
        except KeyError as error:
            raise KlingProviderContractError(
                f"Kling Create Task response has unsupported status: {provider_status!r}."
            ) from error

    @staticmethod
    def _milliseconds_to_utc(value: int, field_name: str) -> datetime:
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        except (OverflowError, OSError, ValueError) as error:
            raise KlingMalformedResponseError(
                f"Kling Create Task response has an invalid {field_name} timestamp."
            ) from error

    @classmethod
    def _optional_timestamp(cls, value, field_name, tolerate_invalid):
        if value is None: return None
        try: return cls._milliseconds_to_utc(value, field_name)
        except KlingMalformedResponseError:
            if tolerate_invalid: return None
            raise
