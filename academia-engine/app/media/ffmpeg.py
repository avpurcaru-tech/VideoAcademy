import hashlib
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from .contracts import NormalizedVideoArtifact, VideoNormalizationProfile
from .ffprobe import FFprobeAdapter, MediaProbeError, _bounded_stderr
from .process_runner import ProcessRunner


class VideoNormalizationError(RuntimeError):
    """Base sanitized deterministic normalization error."""


class VideoSourceNotFoundError(VideoNormalizationError):
    """Raised when normalization source is not a local file."""


class VideoNormalizationDestinationExistsError(VideoNormalizationError):
    """Raised when publication would overwrite an existing destination."""


class FFmpegExecutionError(VideoNormalizationError):
    """Raised when FFmpeg returns a failure or cannot execute."""


class NormalizedVideoValidationError(VideoNormalizationError):
    """Raised when encoded output is empty or does not match its profile."""


class FFmpegVideoNormalizer:
    def __init__(
        self,
        runner: ProcessRunner,
        probe: FFprobeAdapter,
        *,
        executable: str = "ffmpeg",
        timeout_seconds: float | None = None,
    ) -> None:
        self._runner = runner
        self._probe = probe
        self._executable = executable
        self._timeout_seconds = timeout_seconds

    def normalize_video(
        self,
        source: Path,
        destination: Path,
        profile: VideoNormalizationProfile,
        *,
        overwrite: bool = False,
    ) -> NormalizedVideoArtifact:
        source = Path(source)
        destination = Path(destination)
        if not source.is_file():
            raise VideoSourceNotFoundError("The normalization source file does not exist.")
        if destination.exists() and not overwrite:
            raise VideoNormalizationDestinationExistsError(
                "The normalization destination already exists."
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="wb",
                dir=destination.parent,
                prefix=f".{destination.stem}.",
                suffix=f".part{destination.suffix}",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
            args = self._arguments(source, temporary_path, profile)
            try:
                result = self._runner.run(args, timeout_seconds=self._timeout_seconds)
            except Exception as error:
                raise FFmpegExecutionError("ffmpeg could not be executed.") from error
            if result.exit_code != 0:
                summary = _bounded_stderr(result.stderr)
                detail = f"; stderr: {summary}" if summary else ""
                raise FFmpegExecutionError(
                    f"ffmpeg failed with exit code {result.exit_code}{detail}."
                )
            byte_size = temporary_path.stat().st_size
            if byte_size <= 0:
                raise NormalizedVideoValidationError("ffmpeg produced an empty output file.")
            with temporary_path.open("r+b") as output:
                os.fsync(output.fileno())
            sha256 = _sha256(temporary_path)
            try:
                media_info = self._probe.probe_video(temporary_path)
            except MediaProbeError as error:
                raise NormalizedVideoValidationError(
                    "Normalized video output could not be inspected safely."
                ) from error
            self._validate_profile(media_info.width, media_info.height, media_info.frame_rate, profile)
            if destination.exists() and not overwrite:
                raise VideoNormalizationDestinationExistsError(
                    "The normalization destination already exists."
                )
            os.replace(temporary_path, destination)
            temporary_path = None
            return NormalizedVideoArtifact(
                local_path=destination,
                byte_size=byte_size,
                sha256=sha256,
                media_info=media_info.model_copy(update={"local_path": destination}),
            )
        except VideoNormalizationError:
            raise
        except OSError as error:
            raise VideoNormalizationError("Normalized video could not be published atomically.") from error
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _arguments(self, source: Path, destination: Path, profile: VideoNormalizationProfile) -> list[str]:
        return [
            self._executable,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-vf",
            f"scale={profile.width}:{profile.height}",
            "-r",
            _number(profile.frame_rate),
            "-c:v",
            profile.video_codec,
            "-pix_fmt",
            profile.pixel_format,
            "-c:a",
            profile.audio_codec,
            "-movflags",
            "+faststart",
            str(destination),
        ]

    @staticmethod
    def _validate_profile(width: int, height: int, frame_rate: float, profile: VideoNormalizationProfile) -> None:
        if width != profile.width or height != profile.height:
            raise NormalizedVideoValidationError(
                "Normalized video dimensions do not match the requested profile."
            )
        if abs(frame_rate - profile.frame_rate) > 0.001:
            raise NormalizedVideoValidationError(
                "Normalized video frame rate does not match the requested profile."
            )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _number(value: float) -> str:
    return str(int(value)) if value.is_integer() else str(value)
