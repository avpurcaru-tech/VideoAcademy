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


class EpisodeSceneStatus(str, Enum):
    PENDING = "pending"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


class ProductionFailureStage(str, Enum):
    VIDEO_REQUEST_RESOLUTION = "video_request_resolution"
    VIDEO_PROVIDER_CONFIGURATION = "video_provider_configuration"
    VIDEO_SUBMISSION = "video_submission"
    VIDEO_POLLING = "video_polling"
    VIDEO_DOWNLOAD = "video_download"
    VIDEO_ASSEMBLY = "video_assembly"
    REGISTRY_PERSISTENCE = "registry_persistence"


class EpisodeTransitionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    kind: TimelineTransitionKind
    duration_seconds: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def supported_policy(self) -> "EpisodeTransitionPolicy":
        if self.kind not in {TimelineTransitionKind.CUT, TimelineTransitionKind.FADE, TimelineTransitionKind.DISSOLVE}:
            raise ValueError("Episode production supports only cut, fade, and dissolve transitions.")
        if self.kind == TimelineTransitionKind.CUT and self.duration_seconds not in {None, 0}:
            raise ValueError("Cut transition duration must be zero or absent.")
        if self.kind in {TimelineTransitionKind.FADE, TimelineTransitionKind.DISSOLVE} and (self.duration_seconds is None or self.duration_seconds <= 0):
            raise ValueError("Fade and dissolve transition duration must be positive.")
        return self


class EpisodeProductionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    production_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    video_requests: tuple[VideoGenerationRequest, ...] = Field(min_length=2)
    generation_request_references: tuple[GenerationRequestReference, ...] = Field(min_length=2)
    source_scene_ids: tuple[str, ...] = ()
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
        if self.source_scene_ids and len(self.source_scene_ids) != len(self.video_requests):
            raise ValueError("Source scene traceability must align with episode scenes.")
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
    source_scene_id: str | None = None
    order: int = Field(ge=0)
    generation_request_reference: GenerationRequestReference
    provider_task_id: str | None = None
    external_correlation_id: str | None = None
    normalized_status: GenerationTaskStatus | None = None
    production_status: EpisodeSceneStatus = EpisodeSceneStatus.PENDING
    local_path: Path | None = None
    artifact_id: str | None = None
    byte_size: int | None = Field(default=None, gt=0)
    sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    content_type: str | None = None

    @model_validator(mode="before")
    @classmethod
    def infer_legacy_production_status(cls, value):
        if not isinstance(value, dict) or "production_status" in value:
            return value
        copied = dict(value)
        provider_status = copied.get("normalized_status")
        if copied.get("local_path") is not None:
            copied["production_status"] = EpisodeSceneStatus.READY
        elif provider_status == GenerationTaskStatus.FAILED or provider_status == "failed":
            copied["production_status"] = EpisodeSceneStatus.FAILED
        elif copied.get("provider_task_id") is not None:
            copied["production_status"] = EpisodeSceneStatus.GENERATING
        else:
            copied["production_status"] = EpisodeSceneStatus.PENDING
        return copied


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
    failed_scene_id: str | None = None
    failure_stage: ProductionFailureStage | None = None
    failure_category: str | None = Field(default=None, max_length=100)
    safe_message: str | None = Field(default=None, max_length=500)
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Production timestamps must be timezone-aware.")
        return value
