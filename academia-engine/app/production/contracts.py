import json
from datetime import datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import GenerationTaskStatus, VideoGenerationRequest
from app.timeline import RenderedTimelineArtifact, TimelineTransitionKind
from .request_reference import GenerationRequestReference


class EpisodeProductionStatus(str, Enum):
    PENDING = "pending"
    GENERATING = "generating"
    ASSEMBLING = "assembling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class EpisodeTransitionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    kind: TimelineTransitionKind
    duration_seconds: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def supported_policy(self) -> "EpisodeTransitionPolicy":
        if self.kind not in {TimelineTransitionKind.CUT, TimelineTransitionKind.FADE}:
            raise ValueError("Episode production supports only cut and fade transitions.")
        if self.kind == TimelineTransitionKind.CUT and self.duration_seconds not in {None, 0}:
            raise ValueError("Cut transition duration must be zero or absent.")
        if self.kind == TimelineTransitionKind.FADE and (self.duration_seconds is None or self.duration_seconds <= 0):
            raise ValueError("Fade transition duration must be positive.")
        return self


class EpisodeProductionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    production_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    video_requests: tuple[VideoGenerationRequest, ...] = Field(min_length=2)
    generation_request_references: tuple[GenerationRequestReference, ...] = Field(min_length=2)
    provider: str = Field(min_length=1, max_length=100)
    scene_output_directory: Path
    final_output_path: Path
    media_workspace: Path
    transition_policy: EpisodeTransitionPolicy

    @model_validator(mode="after")
    def requests_must_be_unambiguous(self) -> "EpisodeProductionRequest":
        ids = [request.request_id for request in self.video_requests]
        if len(ids) != len(set(ids)):
            raise ValueError("Episode video request IDs must be unique.")
        if len(self.generation_request_references) != len(self.video_requests):
            raise ValueError("Every episode scene requires one generation request reference.")
        references = [reference.reference_id for reference in self.generation_request_references]
        if len(references) != len(set(references)):
            raise ValueError("Episode generation request references must be unique.")
        return self

    def to_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_json(cls, value: str) -> "EpisodeProductionRequest":
        return cls.model_validate_json(value)


class EpisodeSceneResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scene_id: str
    order: int = Field(ge=0)
    generation_request_reference: GenerationRequestReference
    provider_task_id: str | None = None
    external_correlation_id: str | None = None
    normalized_status: GenerationTaskStatus | None = None
    local_path: Path | None = None
    artifact_id: str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class EpisodeProductionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    production_id: str
    status: EpisodeProductionStatus
    scenes: tuple[EpisodeSceneResult, ...]
    final_artifact: RenderedTimelineArtifact | None = None


class ProductionRecord(BaseModel):
    """Durable prompt-free state. Lifecycle: pending -> generating -> assembling -> succeeded; active stages may fail."""
    model_config = ConfigDict(extra="forbid")
    production_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    status: EpisodeProductionStatus
    provider: str
    scenes: tuple[EpisodeSceneResult, ...]
    scene_output_directory: Path
    final_output_path: Path
    media_workspace: Path
    transition_policy: EpisodeTransitionPolicy
    final_artifact: RenderedTimelineArtifact | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Production timestamps must be timezone-aware.")
        return value
