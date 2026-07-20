from typing import Protocol, runtime_checkable

from .contracts import MusicGenerationRequest, MusicGenerationTask


class MusicProvider(Protocol):
    """Independent provider-neutral asynchronous music generation contract."""
    def submit_generation(self,request: MusicGenerationRequest) -> MusicGenerationTask: ...
    def get_task_by_id(self,provider_task_id: str) -> MusicGenerationTask: ...


@runtime_checkable
class MusicExternalIdProvider(Protocol):
    """Optional capability; providers must not fabricate correlation lookup."""
    def get_task_by_external_id(self,external_correlation_id: str) -> MusicGenerationTask: ...
