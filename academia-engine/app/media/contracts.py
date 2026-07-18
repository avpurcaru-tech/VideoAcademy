from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class MediaProbeResult(BaseModel):
    """Normalized media facts with no raw tool response attached."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    local_path: Path
    duration_seconds: float = Field(gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    frame_rate: float = Field(gt=0)
    video_codec: str = Field(min_length=1)
    audio_codec: str | None = None
    has_audio: bool
    container_format: str = Field(min_length=1)


class VideoNormalizationProfile(BaseModel):
    """Deterministic target properties for one normalized pipeline video."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    width: int = Field(gt=0)
    height: int = Field(gt=0)
    frame_rate: float = Field(gt=0)
    video_codec: str = Field(min_length=1)
    audio_codec: str = Field(min_length=1)
    pixel_format: str = Field(min_length=1)

    @classmethod
    def academia_default(cls) -> "VideoNormalizationProfile":
        return cls(
            width=1280,
            height=720,
            frame_rate=30,
            video_codec="libx264",
            audio_codec="aac",
            pixel_format="yuv420p",
        )


class NormalizedVideoArtifact(BaseModel):
    """Durable metadata for an atomically published normalized video."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    local_path: Path
    byte_size: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    media_info: MediaProbeResult


class ConcatenatedVideoArtifact(BaseModel):
    """Durable metadata for an atomically published scene sequence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    local_path: Path
    byte_size: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    media_info: MediaProbeResult
    source_count: int = Field(ge=2)


class AudioLoudnessProfile(BaseModel):
    """Explicit deterministic EBU-style loudness targets."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    integrated_lufs: float = Field(ge=-70, le=0)
    loudness_range_lu: float = Field(gt=0, le=50)
    true_peak_db: float = Field(ge=-9, le=0)

    @classmethod
    def academia_default(cls) -> "AudioLoudnessProfile":
        return cls(integrated_lufs=-16.0, loudness_range_lu=11.0, true_peak_db=-1.5)


class LoudnessNormalizedVideoArtifact(BaseModel):
    """Durable metadata excluding transient loudnorm measurements."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    local_path: Path
    byte_size: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    media_info: MediaProbeResult


class VideoAssemblyRequest(BaseModel):
    """Ordered inputs and explicit policies for one isolated assembly workflow."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sources: tuple[Path, ...]
    destination: Path
    workspace: Path
    normalization_profile: VideoNormalizationProfile
    loudness_profile: AudioLoudnessProfile
    overwrite: bool = False


class AssembledVideoArtifact(BaseModel):
    """Durable final metadata with no intermediate workspace paths."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    local_path: Path
    byte_size: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    media_info: MediaProbeResult
    source_count: int = Field(ge=2)
