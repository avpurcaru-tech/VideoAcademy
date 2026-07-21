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


class ProjectRecord(BaseModel):
    """Prompt-free durable coordination state; provider payloads and URLs are forbidden."""
    model_config=ConfigDict(extra="forbid",frozen=True)
    project_id: str=Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    episode_id: str=Field(min_length=1,max_length=200)
    status: ProjectStatus
    video_production_id: str=Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    music_task_id: str|None=Field(default=None,pattern=r"^[A-Za-z0-9_-]+$")
    lyrics_path: Path
    music_directory: Path
    video_directory: Path
    final_directory: Path
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at","updated_at")
    @classmethod
    def aware(cls,value):
        if value.tzinfo is None or value.utcoffset() is None: raise ValueError("Project timestamps must be timezone-aware.")
        return value
