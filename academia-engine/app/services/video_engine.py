from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field

from app.models import GenerationTask, GenerationTaskStatus, VideoArtifact, VideoGenerationRequest
from app.providers.video_provider import VideoProvider

from .artifact_downloader import ArtifactDownloadError, VideoArtifactDownloader
from .task_registry import (
    ArtifactRecord,
    GenerationTaskRecord,
    TaskRegistry,
    TaskRegistryError,
    TaskRegistryNotFoundError,
)


class VideoEngineError(RuntimeError):
    """Base safe error exposed by the provider-neutral orchestration boundary."""


class UnknownVideoProviderError(VideoEngineError):
    """Raised when no configured provider matches the requested provider name."""


class VideoTaskNotFoundError(VideoEngineError):
    """Raised when the durable registry has no requested task."""


class ProviderTaskIdMismatchError(VideoEngineError):
    """Raised when a provider returns a different task than requested."""


class VideoTaskNotSucceededError(VideoEngineError):
    """Raised when download is requested before successful generation."""


class NoDownloadableVideoArtifactError(VideoEngineError):
    """Raised when a succeeded task has no usable video artifact."""


class MultipleDownloadableVideoArtifactsError(VideoEngineError):
    """Raised when artifact selection would be ambiguous."""


class VideoEngineArtifactDownloadError(VideoEngineError):
    """Raised when an artifact cannot be downloaded safely."""


class VideoProviderOperationError(VideoEngineError):
    """Raised when a provider operation fails without leaking provider details."""


class VideoEngineRegistryError(VideoEngineError):
    """Raised when durable task state cannot be read or written safely."""


class VideoEngineTimeoutError(VideoEngineError):
    """Raised when polling reaches its monotonic deadline."""


class VideoEngineAttemptsExceededError(VideoEngineError):
    """Raised when polling consumes its configured refresh limit."""


class VideoEngineTaskFailedError(VideoEngineError):
    """Raised when wait-and-download observes a failed terminal task."""


class VideoEngineContractError(VideoEngineError):
    """Raised when durable normalized state is outside the workflow contract."""


class VideoPollingPolicy(BaseModel):
    """Validated provider-neutral polling limits."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    interval_seconds: float = Field(gt=0)
    timeout_seconds: float = Field(gt=0)
    max_attempts: int | None = Field(default=None, gt=0)


class VideoEngine:
    """Coordinates asynchronous video providers, durable state, and downloads."""

    def __init__(
        self,
        providers: Mapping[str, VideoProvider],
        registry: TaskRegistry,
        downloader: VideoArtifactDownloader,
        *,
        default_provider: str | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._providers = dict(providers)
        self._registry = registry
        self._downloader = downloader
        self._default_provider = default_provider
        self._monotonic_clock = monotonic_clock
        self._sleeper = sleeper

    def submit(
        self,
        request: VideoGenerationRequest,
        provider: str | None = None,
    ) -> GenerationTaskRecord:
        provider_name = provider or self._default_provider
        selected = self._resolve_provider(provider_name)
        try:
            task = selected.submit_generation(request)
        except Exception as error:
            raise VideoProviderOperationError("Video generation submission failed.") from error
        if task.provider_name != provider_name:
            raise VideoProviderOperationError("The provider returned an inconsistent provider name.")
        now = _now()
        record = GenerationTaskRecord(
            provider=provider_name,
            provider_task_id=task.external_task_id,
            external_correlation_id=task.external_correlation_id,
            normalized_status=task.normalized_status,
            created_at=task.submitted_at or now,
            updated_at=task.updated_at or now,
        )
        try:
            self._registry.create(record)
            return self._registry.load(record.provider_task_id)
        except TaskRegistryError as error:
            raise VideoEngineRegistryError("The submitted task could not be stored safely.") from error

    def refresh(self, provider_task_id: str) -> GenerationTaskRecord:
        existing = self._load(provider_task_id)
        task = self._query(existing.provider, provider_task_id)
        refreshed = self._refreshed_record(existing, task)
        return self._persist(refreshed)

    def reconcile_existing_task(self, provider: str, provider_task_id: str) -> GenerationTaskRecord:
        """Query and durably adopt an existing provider task without submitting work."""
        task = self._query(provider, provider_task_id)
        now = _now()
        if self._registry.exists(provider_task_id):
            existing = self._load(provider_task_id)
            if existing.provider != provider:
                raise VideoEngineContractError("The existing task belongs to a different provider.")
            record = existing.model_copy(update={
                "external_correlation_id": task.external_correlation_id,
                "normalized_status": task.normalized_status,
                "updated_at": task.updated_at or now,
            })
            return self._persist(record)
        record = GenerationTaskRecord(
            provider=provider,
            provider_task_id=task.external_task_id,
            external_correlation_id=task.external_correlation_id,
            normalized_status=task.normalized_status,
            created_at=task.submitted_at or now,
            updated_at=task.updated_at or now,
        )
        try:
            self._registry.create(record)
            return self._registry.load(provider_task_id)
        except TaskRegistryError as error:
            raise VideoEngineRegistryError("The reconciled task could not be stored safely.") from error

    def download(self, provider_task_id: str, destination: Path) -> GenerationTaskRecord:
        existing = self._load(provider_task_id)
        task = self._query(existing.provider, provider_task_id)
        refreshed = self._persist(self._refreshed_record(existing, task))
        if task.normalized_status != GenerationTaskStatus.SUCCEEDED:
            raise VideoTaskNotSucceededError("The video task has not succeeded.")
        artifact = self._single_video_artifact(task.artifacts)
        try:
            downloaded = self._downloader.download_video_artifact(artifact, Path(destination))
        except Exception as error:
            message = "The video artifact could not be downloaded safely."
            if isinstance(error, ArtifactDownloadError):
                raise VideoEngineArtifactDownloadError(message) from error
            raise VideoEngineArtifactDownloadError(message) from error
        completed = refreshed.model_copy(
            update={
                "artifact": ArtifactRecord(
                    artifact_id=downloaded.artifact_id,
                    local_path=downloaded.local_path,
                    byte_size=downloaded.byte_size,
                    sha256=downloaded.sha256,
                    content_type=downloaded.content_type,
                )
            }
        )
        return self._persist(completed)

    def wait_until_terminal(
        self,
        provider_task_id: str,
        policy: VideoPollingPolicy,
    ) -> GenerationTaskRecord:
        # Fail before starting the clock or touching the provider when no manifest exists.
        self._load(provider_task_id)
        deadline = self._monotonic_clock() + policy.timeout_seconds
        attempts = 0

        while True:
            record = self.refresh(provider_task_id)
            attempts += 1
            if record.normalized_status in {
                GenerationTaskStatus.SUCCEEDED,
                GenerationTaskStatus.FAILED,
            }:
                return record
            if policy.max_attempts is not None and attempts >= policy.max_attempts:
                raise VideoEngineAttemptsExceededError(
                    "Video polling reached the configured attempt limit."
                )

            remaining = deadline - self._monotonic_clock()
            if remaining <= 0:
                raise VideoEngineTimeoutError("Video polling timed out.")
            self._sleeper(min(policy.interval_seconds, remaining))
            if self._monotonic_clock() >= deadline:
                raise VideoEngineTimeoutError("Video polling timed out.")

    def wait_and_download(
        self,
        provider_task_id: str,
        destination: Path,
        policy: VideoPollingPolicy,
    ) -> GenerationTaskRecord:
        terminal = self.wait_until_terminal(provider_task_id, policy)
        if terminal.normalized_status == GenerationTaskStatus.FAILED:
            raise VideoEngineTaskFailedError("The video task failed.")
        return self.download(provider_task_id, destination)

    def generate(
        self,
        request: VideoGenerationRequest,
        destination: Path,
        policy: VideoPollingPolicy,
        provider: str | None = None,
    ) -> GenerationTaskRecord:
        """Submit once, wait, and download; repeated calls intentionally submit new tasks."""
        submitted = self.submit(request, provider=provider)
        return self.wait_and_download(submitted.provider_task_id, destination, policy)

    def resume(
        self,
        provider_task_id: str,
        destination: Path,
        policy: VideoPollingPolicy,
    ) -> GenerationTaskRecord:
        """Continue an existing durable workflow without submitting another task."""
        existing = self._load(provider_task_id)
        if existing.normalized_status in {
            GenerationTaskStatus.SUBMITTED,
            GenerationTaskStatus.PROCESSING,
        }:
            return self.wait_and_download(provider_task_id, destination, policy)
        if existing.normalized_status == GenerationTaskStatus.SUCCEEDED:
            if existing.artifact is not None:
                return existing
            return self.download(provider_task_id, destination)
        if existing.normalized_status == GenerationTaskStatus.FAILED:
            raise VideoEngineTaskFailedError("The video task failed.")
        raise VideoEngineContractError("The registry contains an unsupported normalized status.")

    def _resolve_provider(self, provider_name: str | None) -> VideoProvider:
        if not provider_name or provider_name not in self._providers:
            raise UnknownVideoProviderError("The requested video provider is not configured.")
        return self._providers[provider_name]

    def _load(self, provider_task_id: str) -> GenerationTaskRecord:
        try:
            return self._registry.load(provider_task_id)
        except TaskRegistryNotFoundError as error:
            raise VideoTaskNotFoundError("The video task is missing from the registry.") from error
        except TaskRegistryError as error:
            raise VideoEngineRegistryError("The video task registry could not be read safely.") from error

    def _query(self, provider_name: str, provider_task_id: str) -> GenerationTask:
        provider = self._resolve_provider(provider_name)
        try:
            task = provider.get_task_by_id(provider_task_id)
        except Exception as error:
            raise VideoProviderOperationError("The video provider task query failed.") from error
        if task.external_task_id != provider_task_id:
            raise ProviderTaskIdMismatchError("The provider returned a different task ID.")
        return task

    @staticmethod
    def _refreshed_record(existing: GenerationTaskRecord, task: GenerationTask) -> GenerationTaskRecord:
        return existing.model_copy(
            update={
                "normalized_status": task.normalized_status,
                "updated_at": task.updated_at or _now(),
            }
        )

    def _persist(self, record: GenerationTaskRecord) -> GenerationTaskRecord:
        try:
            self._registry.update(record)
            return self._registry.load(record.provider_task_id)
        except TaskRegistryError as error:
            raise VideoEngineRegistryError("The video task registry could not be updated safely.") from error

    @staticmethod
    def _single_video_artifact(artifacts: list[VideoArtifact]) -> VideoArtifact:
        videos = [
            artifact
            for artifact in artifacts
            if artifact.content_type is None or artifact.content_type.lower().startswith("video")
        ]
        if not videos:
            raise NoDownloadableVideoArtifactError("No downloadable video artifact is available.")
        if len(videos) > 1:
            raise MultipleDownloadableVideoArtifactsError(
                "Multiple downloadable video artifacts are available."
            )
        return videos[0]


def _now() -> datetime:
    return datetime.now(timezone.utc)
