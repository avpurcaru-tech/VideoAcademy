import hashlib
import os
from collections.abc import Sequence
from pathlib import Path
from tempfile import NamedTemporaryFile

from .contracts import ConcatenatedVideoArtifact, MediaProbeResult
from .ffprobe import FFprobeAdapter, MediaProbeError, _bounded_stderr
from .process_runner import ProcessRunner


DEFAULT_DURATION_TOLERANCE_SECONDS = 0.25
_FRAME_RATE_TOLERANCE = 0.001


class VideoConcatenationError(RuntimeError):
    """Base provider-neutral concatenation error."""


class InsufficientVideoSourcesError(VideoConcatenationError):
    """Raised when fewer than two scene files are supplied."""


class ConcatenationSourceNotFoundError(VideoConcatenationError):
    """Raised when a source is not an existing local file."""


class IncompatibleVideoDimensionsError(VideoConcatenationError):
    """Raised when normalized source dimensions differ."""


class IncompatibleVideoFrameRateError(VideoConcatenationError):
    """Raised when normalized source frame rates differ."""


class IncompatibleVideoCodecError(VideoConcatenationError):
    """Raised when source video codecs differ under strict stream-copy policy."""


class MixedAudioPresenceError(VideoConcatenationError):
    """Raised when only some sources contain audio."""


class IncompatibleAudioCodecError(VideoConcatenationError):
    """Raised when audio-bearing sources use different codecs."""


class ConcatManifestError(VideoConcatenationError):
    """Raised when a safe temporary concat manifest cannot be produced."""


class FFmpegConcatError(VideoConcatenationError):
    """Raised when the single FFmpeg concatenation process fails."""


class EmptyConcatenatedOutputError(VideoConcatenationError):
    """Raised when FFmpeg succeeds without producing media bytes."""


class ConcatenatedDurationMismatchError(VideoConcatenationError):
    """Raised when output duration differs from the sum of source durations."""


class ConcatenatedMediaMismatchError(VideoConcatenationError):
    """Raised when output dimensions or frame rate differ from the sources."""


class ConcatenationDestinationExistsError(VideoConcatenationError):
    """Raised when publication would overwrite an existing destination."""


class FFmpegVideoConcatenator:
    """Strict concat-demuxer orchestration for already-normalized local videos."""

    def __init__(
        self,
        runner: ProcessRunner,
        probe: FFprobeAdapter,
        *,
        executable: str = "ffmpeg",
        timeout_seconds: float | None = None,
        duration_tolerance_seconds: float = DEFAULT_DURATION_TOLERANCE_SECONDS,
    ) -> None:
        if duration_tolerance_seconds < 0:
            raise ValueError("Duration tolerance must not be negative.")
        self._runner = runner
        self._probe = probe
        self._executable = executable
        self._timeout_seconds = timeout_seconds
        self._duration_tolerance_seconds = duration_tolerance_seconds

    def concatenate_videos(
        self,
        sources: Sequence[Path],
        destination: Path,
        *,
        overwrite: bool = False,
    ) -> ConcatenatedVideoArtifact:
        source_paths = [Path(source) for source in sources]
        destination = Path(destination)
        if len(source_paths) < 2:
            raise InsufficientVideoSourcesError("At least two video sources are required.")
        for source in source_paths:
            if not source.is_file():
                raise ConcatenationSourceNotFoundError(
                    f"Concatenation source does not exist: {source}"
                )
        if destination.exists() and not overwrite:
            raise ConcatenationDestinationExistsError(
                "The concatenation destination already exists."
            )

        media = [self._probe.probe_video(source) for source in source_paths]
        self._validate_sources(media)
        expected_duration = sum(item.duration_seconds for item in media)
        destination.parent.mkdir(parents=True, exist_ok=True)
        manifest_path: Path | None = None
        temporary_output: Path | None = None
        try:
            manifest_path = self._write_manifest(source_paths, destination.parent)
            with NamedTemporaryFile(
                mode="wb",
                dir=destination.parent,
                prefix=f".{destination.stem}.",
                suffix=f".part{destination.suffix}",
                delete=False,
            ) as temporary:
                temporary_output = Path(temporary.name)
            args = [
                self._executable,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(manifest_path),
                "-c",
                "copy",
                str(temporary_output),
            ]
            try:
                result = self._runner.run(args, timeout_seconds=self._timeout_seconds)
            except Exception as error:
                raise FFmpegConcatError("ffmpeg concat could not be executed.") from error
            if result.exit_code != 0:
                summary = _bounded_stderr(result.stderr)
                detail = f"; stderr: {summary}" if summary else ""
                raise FFmpegConcatError(
                    f"ffmpeg concat failed with exit code {result.exit_code}{detail}."
                )
            byte_size = temporary_output.stat().st_size
            if byte_size <= 0:
                raise EmptyConcatenatedOutputError("ffmpeg concat produced an empty output file.")
            with temporary_output.open("r+b") as output:
                os.fsync(output.fileno())
            sha256 = _sha256(temporary_output)
            try:
                output_media = self._probe.probe_video(temporary_output)
            except MediaProbeError as error:
                raise ConcatenatedMediaMismatchError(
                    "Concatenated output could not be inspected safely."
                ) from error
            self._validate_output(media[0], output_media, expected_duration)
            if destination.exists() and not overwrite:
                raise ConcatenationDestinationExistsError(
                    "The concatenation destination already exists."
                )
            os.replace(temporary_output, destination)
            temporary_output = None
            return ConcatenatedVideoArtifact(
                local_path=destination,
                byte_size=byte_size,
                sha256=sha256,
                media_info=output_media.model_copy(update={"local_path": destination}),
                source_count=len(source_paths),
            )
        except VideoConcatenationError:
            raise
        except OSError as error:
            raise VideoConcatenationError(
                "Concatenated video could not be published atomically."
            ) from error
        finally:
            if temporary_output is not None:
                temporary_output.unlink(missing_ok=True)
            if manifest_path is not None:
                manifest_path.unlink(missing_ok=True)

    @staticmethod
    def _write_manifest(sources: list[Path], directory: Path) -> Path:
        manifest_path: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=directory,
                prefix=".concat.",
                suffix=".txt",
                delete=False,
            ) as manifest:
                manifest_path = Path(manifest.name)
                for source in sources:
                    manifest.write(f"file '{_escape_manifest_path(source.resolve())}'\n")
                manifest.flush()
                os.fsync(manifest.fileno())
            return manifest_path
        except (OSError, ValueError) as error:
            if manifest_path is not None:
                manifest_path.unlink(missing_ok=True)
            raise ConcatManifestError("The temporary concat manifest could not be created.") from error

    @staticmethod
    def _validate_sources(media: list[MediaProbeResult]) -> None:
        expected = media[0]
        for item in media[1:]:
            if (item.width, item.height) != (expected.width, expected.height):
                raise IncompatibleVideoDimensionsError(
                    "Video source dimensions are incompatible."
                )
            if abs(item.frame_rate - expected.frame_rate) > _FRAME_RATE_TOLERANCE:
                raise IncompatibleVideoFrameRateError(
                    "Video source frame rates are incompatible."
                )
            if item.video_codec != expected.video_codec:
                raise IncompatibleVideoCodecError("Video source codecs are incompatible.")
            if item.has_audio != expected.has_audio:
                raise MixedAudioPresenceError(
                    "All video sources must consistently contain audio or contain no audio."
                )
            if item.has_audio and item.audio_codec != expected.audio_codec:
                raise IncompatibleAudioCodecError("Video source audio codecs are incompatible.")

    def _validate_output(
        self,
        expected_media: MediaProbeResult,
        output_media: MediaProbeResult,
        expected_duration: float,
    ) -> None:
        if abs(output_media.duration_seconds - expected_duration) > self._duration_tolerance_seconds:
            raise ConcatenatedDurationMismatchError(
                "Concatenated duration is outside the configured tolerance."
            )
        if (output_media.width, output_media.height) != (
            expected_media.width,
            expected_media.height,
        ):
            raise ConcatenatedMediaMismatchError(
                "Concatenated output resolution does not match its sources."
            )
        if abs(output_media.frame_rate - expected_media.frame_rate) > _FRAME_RATE_TOLERANCE:
            raise ConcatenatedMediaMismatchError(
                "Concatenated output frame rate does not match its sources."
            )


def _escape_manifest_path(path: Path) -> str:
    value = str(path)
    if "\n" in value or "\r" in value or "\x00" in value:
        raise ValueError("Concat manifest paths cannot contain control characters.")
    return value.replace("\\", "\\\\").replace("'", "'\\''")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
