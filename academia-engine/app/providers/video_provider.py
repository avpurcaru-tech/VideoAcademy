from typing import Protocol

from app.models import GenerationTask, VideoGenerationRequest


class VideoProvider(Protocol):
    """Provider-neutral asynchronous video generation contract."""

    def submit_generation(self, request: VideoGenerationRequest) -> GenerationTask:
        """Submit one generation request and return its normalized task."""

    def get_task_by_id(self, provider_task_id: str) -> GenerationTask:
        """Retrieve a normalized task by its provider-assigned ID."""

    def get_task_by_external_id(self, external_correlation_id: str) -> GenerationTask:
        """Retrieve a normalized task by its external correlation ID."""
