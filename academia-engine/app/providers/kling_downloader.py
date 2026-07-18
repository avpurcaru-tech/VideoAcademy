import hashlib
import os
from collections.abc import Callable
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from urllib.request import Request, urlopen

from app.models import VideoArtifact
from app.services.artifact_downloader import (
    ArtifactDestinationExistsError,
    ArtifactDownloadError,
    ArtifactDownloadValidationError,
    DownloadedVideoArtifact,
    VideoArtifactDownloader,
)


class KlingVideoArtifactDownloader(VideoArtifactDownloader):
    """Downloads a signed Kling CDN URL without forwarding Kling credentials."""

    def __init__(
        self,
        opener: Callable[..., Any] = urlopen,
        chunk_size: int = 1024 * 1024,
        timeout_seconds: float = 30,
    ) -> None:
        self._opener = opener
        self._chunk_size = chunk_size
        self._timeout_seconds = timeout_seconds

    def download_video_artifact(
        self,
        artifact: VideoArtifact,
        destination: Path,
        *,
        overwrite: bool = False,
    ) -> DownloadedVideoArtifact:
        destination = Path(destination)
        if destination.exists() and not overwrite:
            raise ArtifactDestinationExistsError("The requested output file already exists.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        response: Any | None = None
        try:
            # The signed CDN URL is used directly; no Kling Authorization header is attached.
            request = Request(url=artifact.url, method="GET")
            response = self._opener(request, timeout=self._timeout_seconds)
            status_code = getattr(response, "status", response.getcode())
            if not 200 <= status_code < 300:
                raise ArtifactDownloadValidationError(
                    f"Video download returned HTTP status {status_code}."
                )
            content_type = self._header(response, "Content-Type")
            if content_type and not content_type.lower().startswith("video/"):
                raise ArtifactDownloadValidationError("Video download returned an invalid content type.")
            expected_length = self._content_length(response)
            digest = hashlib.sha256()
            byte_size = 0
            with NamedTemporaryFile(
                mode="wb",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".part",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                while chunk := response.read(self._chunk_size):
                    temporary_file.write(chunk)
                    digest.update(chunk)
                    byte_size += len(chunk)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            if byte_size == 0:
                raise ArtifactDownloadValidationError("Video download returned an empty file.")
            if expected_length is not None and byte_size != expected_length:
                raise ArtifactDownloadValidationError("Video download length does not match Content-Length.")
            if destination.exists() and not overwrite:
                raise ArtifactDestinationExistsError("The requested output file already exists.")
            os.replace(temporary_path, destination)
            temporary_path = None
            return DownloadedVideoArtifact(
                artifact_id=artifact.artifact_id,
                local_path=destination,
                byte_size=byte_size,
                sha256=digest.hexdigest(),
                content_type=content_type,
            )
        except ArtifactDownloadError:
            raise
        except Exception as error:
            raise ArtifactDownloadError("Video download could not be completed.") from error
        finally:
            if response is not None:
                response.close()
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _header(response: Any, name: str) -> str | None:
        headers = getattr(response, "headers", None)
        if headers is None:
            return None
        value = headers.get(name)
        return value if isinstance(value, str) else None

    def _content_length(self, response: Any) -> int | None:
        raw_length = self._header(response, "Content-Length")
        if raw_length is None:
            return None
        try:
            length = int(raw_length)
        except ValueError as error:
            raise ArtifactDownloadValidationError("Video download has an invalid Content-Length.") from error
        if length < 0:
            raise ArtifactDownloadValidationError("Video download has an invalid Content-Length.")
        return length
