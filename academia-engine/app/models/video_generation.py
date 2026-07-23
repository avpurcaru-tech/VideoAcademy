from datetime import datetime
from enum import Enum
from typing import Any, Literal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .video_request import VideoRequest
from app.visual_references import SceneVisualReference
from app.scene_first_frames import SceneFirstFramePlan


class GenerationTaskStatus(str, Enum):
    SUBMITTED = "submitted"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class VideoArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=2000)
    content_type: str | None = Field(default=None, max_length=100)
    watermark_url: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0)


class GenerationTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    external_task_id: str = Field(min_length=1, max_length=200)
    provider_name: str = Field(min_length=1, max_length=100)
    provider_status: str = Field(min_length=1, max_length=100)
    normalized_status: GenerationTaskStatus
    provider_request_id: str | None = None
    provider_code: int | None = None
    provider_message: str | None = None
    external_correlation_id: str | None = None
    error_message: str | None = None
    artifacts: list[VideoArtifact] = Field(default_factory=list)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    submitted_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None


class CharacterReferenceImage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    character_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    local_path: Path
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_type: str = Field(pattern=r"^image/(png|jpeg|webp)$")


class VideoGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    video_request: VideoRequest
    character_reference_images: tuple[CharacterReferenceImage, ...] = ()
    # Durable identity source; never submitted as the provider's literal opening frame.
    identity_visual_reference: SceneVisualReference | None = None
    scene_first_frame_plan: SceneFirstFramePlan | None = None
    scene_visual_reference: SceneVisualReference | None = None
    planned_shot_id: str|None=Field(default=None,pattern=r"^[a-z0-9][a-z0-9_-]*$")

    @model_validator(mode="after")
    def references_match_characters(self):
        ids=[value.character_id for value in self.character_reference_images]
        if len(ids)!=len(set(ids)): raise ValueError("Character reference image IDs must be unique.")
        if not set(ids)<=set(value.id for value in self.video_request.characters):
            raise ValueError("Character reference image must belong to a scene character.")
        return self


class VideoGenerationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    scene_number: int = Field(ge=1)
    provider_name: str = Field(min_length=1, max_length=100)
    external_task_id: str | None = Field(default=None, max_length=200)
    provider_status: str | None = Field(default=None, max_length=100)
    normalized_status: GenerationTaskStatus = GenerationTaskStatus.SUBMITTED
    error_code: str | None = Field(default=None, max_length=100)
    error_message: str | None = Field(default=None, max_length=2000)
    artifacts: list[VideoArtifact] = Field(default_factory=list)
    submitted_at: datetime | None = None
    completed_at: datetime | None = None
    status: Literal["queued", "completed", "failed", "mocked"] = "queued"
    is_mock: bool = False
    asset_reference: str | None = None
