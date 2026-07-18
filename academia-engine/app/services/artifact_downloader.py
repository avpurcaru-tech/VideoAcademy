from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.models import VideoArtifact


class DownloadedVideoArtifact(BaseModel):
    """Durable local metadata that intentionally excludes the source URL."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(min_length=1)
    local_path: Path
    byte_size: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_type: str | None = None


class ArtifactDownloadError(RuntimeError):
    """Safe base error for artifact download failures."""


class ArtifactDestinationExistsError(ArtifactDownloadError):
    """Raised when a final destination already exists and overwrite was not requested."""


class ArtifactDownloadValidationError(ArtifactDownloadError):
    """Raised when a CDN response is not a complete usable video artifact."""


class VideoArtifactNotFoundError(ArtifactDownloadError):
    """Raised when a succeeded task has no video artifact to download."""


class VideoArtifactAmbiguityError(ArtifactDownloadError):
    """Raised when a task has multiple videos and no selection rule is authorized."""


class VideoArtifactDownloader(ABC):
    """Provider-neutral contract for persisting a transient video artifact."""

    @abstractmethod
    def download_video_artifact(
        self,
        artifact: VideoArtifact,
        destination: Path,
        *,
        overwrite: bool = False,
    ) -> DownloadedVideoArtifact:
        """Download one artifact into a durable local file."""
