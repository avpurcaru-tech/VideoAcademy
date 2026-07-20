from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import GenerationTaskStatus
from app.song import LyricsPlan, MusicPlan


SUPPORTED_AUDIO_CONTENT_TYPES={"audio/mpeg":".mp3","audio/wav":".wav"}


class MusicContract(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True,allow_inf_nan=False)


class MusicGenerationRequest(MusicContract):
    song_id: str = Field(min_length=1,max_length=200)
    title: str = Field(min_length=1,max_length=500)
    lyrics: LyricsPlan
    music_plan: MusicPlan

    @field_validator("song_id","title")
    @classmethod
    def safe_text(cls,value: str):
        if not value.strip() or "\0" in value: raise ValueError("Music request text is invalid.")
        return value

    @model_validator(mode="after")
    def consistent_identity(self):
        if self.song_id!=self.lyrics.song_id or self.song_id!=self.music_plan.song_id:
            raise ValueError("Music request song IDs must match.")
        return self


class GeneratedAudioArtifact(MusicContract):
    """Transient provider artifact; download_url must never enter durable state."""
    artifact_id: str = Field(min_length=1,max_length=200)
    download_url: str = Field(min_length=1,max_length=4000)
    content_type: str = Field(min_length=1,max_length=100)


class GeneratedMusicVariant(MusicContract):
    """Safe one-based view of a transient generated artifact."""
    variant_index: int = Field(gt=0)
    artifact_id: str = Field(min_length=1,max_length=200)
    content_type: str = Field(min_length=1,max_length=100)


class MusicGenerationTask(MusicContract):
    provider: str = Field(min_length=1,max_length=100)
    provider_task_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    external_correlation_id: str | None=None
    normalized_status: GenerationTaskStatus
    artifacts: tuple[GeneratedAudioArtifact,...]=()
    created_at: datetime | None=None
    updated_at: datetime | None=None

    @field_validator("created_at","updated_at")
    @classmethod
    def optional_timestamps_are_aware(cls,value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("Music task timestamps must be timezone-aware.")
        return value


class DurableAudioArtifact(MusicContract):
    artifact_id: str = Field(min_length=1,max_length=200)
    local_path: Path
    byte_size: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_type: str

    @field_validator("content_type")
    @classmethod
    def supported_content_type(cls,value: str):
        normalized=value.lower()
        if normalized not in SUPPORTED_AUDIO_CONTENT_TYPES: raise ValueError("Audio content type is unsupported.")
        return normalized


class MusicGenerationTaskRecord(MusicContract):
    provider: str = Field(min_length=1,max_length=100)
    provider_task_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    external_correlation_id: str | None=None
    normalized_status: GenerationTaskStatus
    created_at: datetime
    updated_at: datetime
    artifact: DurableAudioArtifact | None=None

    @field_validator("created_at","updated_at")
    @classmethod
    def timestamps_are_aware(cls,value):
        if value.tzinfo is None or value.utcoffset() is None: raise ValueError("Music task timestamps must be timezone-aware.")
        return value
