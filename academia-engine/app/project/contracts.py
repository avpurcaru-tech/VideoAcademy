from datetime import datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel,ConfigDict,Field,field_validator


class ProjectStatus(str,Enum):
    PLANNED="planned"
    VIDEO_GENERATING="video_generating"
    MUSIC_GENERATING="music_generating"
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
    LYRICS_GENERATION="lyrics_generation"
    MUSIC_GENERATION="music_generation"
    COMPOSITION="composition"


class ProjectRecord(BaseModel):
    """Prompt-free durable coordination state; provider payloads and URLs are forbidden."""
    model_config=ConfigDict(extra="forbid",frozen=True)
    project_id: str=Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    episode_id: str=Field(min_length=1,max_length=200)
    series_id: str|None=Field(default=None,pattern=r"^[a-z0-9][a-z0-9_-]*$")
    status: ProjectStatus
    video_production_id: str=Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    music_task_id: str|None=Field(default=None,pattern=r"^[A-Za-z0-9_-]+$")
    lyrics_path: Path
    music_directory: Path
    video_directory: Path
    final_directory: Path
    failure_stage: ProjectFailureStage|None=None
    failure_category: str|None=Field(default=None,max_length=100)
    safe_message: str|None=Field(default=None,max_length=500)
    failed_scene_id: str|None=None
    submit_http_status: int|None=None
    submit_provider_code: int|None=None
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
