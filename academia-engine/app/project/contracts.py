from datetime import datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel,ConfigDict,Field,field_validator


class ProjectStatus(str,Enum):
    PLANNED="planned"
    STORYBOARD_GENERATING="storyboard_generating"
    STORYBOARD_READY="storyboard_ready"
    LYRICS_READY="lyrics_ready"
    VIDEO_GENERATING="video_generating"
    VIDEO_PLANNING="video_planning"
    VIDEO_READY="video_ready"
    MUSIC_GENERATING="music_generating"
    MUSIC_READY="music_ready"
    TIMELINES_GENERATING="timelines_generating"
    TIMELINES_READY="timelines_ready"
    COMPOSING="composing"
    COMPLETED="completed"
    FAILED="failed"


class ProjectFailureStage(str,Enum):
    CHARACTER_RESOLUTION="character_resolution"
    SERIES_RESOLUTION="series_resolution"
    STORYBOARD_GENERATION="storyboard_generation"
    EPISODE_GENERATION="episode_generation"
    VIDEO_PLANNING="video_planning"
    VIDEO_REQUEST_RESOLUTION="video_request_resolution"
    VIDEO_PROVIDER_CONFIGURATION="video_provider_configuration"
    VIDEO_SUBMISSION="video_submission"
    VIDEO_POLLING="video_polling"
    VIDEO_DOWNLOAD="video_download"
    VISUAL_IDENTITY_VALIDATION="visual_identity_validation"
    LYRICS_GENERATION="lyrics_generation"
    MUSIC_GENERATION="music_generation"
    COMPOSITION="composition"

class ProjectVideoSceneDiagnostic(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    scene_id: str
    storyboard_section_id: str
    timeline_segment_count: int=Field(ge=1)
    requested_duration: int=Field(ge=1)
    canonical_character_ids: tuple[str,...]
    request_reference_id: str
    prompt_character_count: int=Field(ge=0)

class ProjectCompositionVariant(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    variant_id: str=Field(pattern=r"^variant-[0-9]{2}$")
    status: str=Field(pattern=r"^(completed|failed)$")
    output_path: Path
    byte_size: int|None=Field(default=None,gt=0)
    sha256: str|None=Field(default=None,pattern=r"^[a-f0-9]{64}$")


class ProjectRecord(BaseModel):
    """Prompt-free durable coordination state; provider payloads and URLs are forbidden."""
    model_config=ConfigDict(extra="forbid",frozen=True)
    project_id: str=Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    episode_id: str=Field(min_length=1,max_length=200)
    series_id: str|None=Field(default=None,pattern=r"^[a-z0-9][a-z0-9_-]*$")
    status: ProjectStatus
    orchestration_version: str=Field(default="legacy",pattern=r"^(legacy|storyboard_first)$")
    video_production_id: str=Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    video_provider: str|None=Field(default=None,pattern=r"^[a-z0-9][a-z0-9_-]*$")
    identity_validation_mode: str=Field(default="required",pattern=r"^(required|advisory|disabled)$")
    music_task_id: str|None=Field(default=None,pattern=r"^[A-Za-z0-9_-]+$")
    lyrics_path: Path
    music_directory: Path
    video_directory: Path
    final_directory: Path
    video_coverage_plan_path: Path|None=None
    failure_stage: ProjectFailureStage|None=None
    failure_category: str|None=Field(default=None,max_length=100)
    safe_message: str|None=Field(default=None,max_length=500)
    failure_details: tuple[str,...]=()
    provider_http_status: int|None=None
    provider_request_id: str|None=Field(default=None,max_length=200,pattern=r"^[A-Za-z0-9_-]+$")
    provider_model: str|None=Field(default=None,max_length=200)
    provider_retry_after: str|None=Field(default=None,max_length=100)
    video_plan_diagnostics: tuple[ProjectVideoSceneDiagnostic,...]=()
    composition_variants: tuple[ProjectCompositionVariant,...]=()
    failed_variant_id: str|None=Field(default=None,pattern=r"^variant-[0-9]{2}$")
    composition_master_video_present: bool|None=None
    composition_master_video_duration: float|None=Field(default=None,gt=0)
    composition_audio_present: bool|None=None
    composition_audio_duration: float|None=Field(default=None,gt=0)
    composition_timeline_present: bool|None=None
    composition_timeline_duration: float|None=Field(default=None,gt=0)
    composition_expected_output_path: Path|None=None
    composition_ffmpeg_exit_code: int|None=None
    composition_ffmpeg_error_category: str|None=Field(default=None,max_length=100)
    failed_scene_id: str|None=None
    submit_http_status: int|None=None
    submit_provider_code: int|None=None
    submit_provider_message: str|None=Field(default=None,max_length=200)
    submit_request_id: str|None=Field(default=None,max_length=200)
    submit_provider_task_id: str|None=Field(default=None,pattern=r"^[A-Za-z0-9_-]+$")
    submit_response_shape: tuple[str,...]=()
    query_http_status: int|None=None
    query_provider_code: int|None=None
    query_provider_task_id: str|None=Field(default=None,pattern=r"^[A-Za-z0-9_-]+$")
    query_response_shape: tuple[str,...]=()
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at","updated_at")
    @classmethod
    def aware(cls,value):
        if value.tzinfo is None or value.utcoffset() is None: raise ValueError("Project timestamps must be timezone-aware.")
        return value
