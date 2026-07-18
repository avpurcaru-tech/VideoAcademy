import hashlib
import json
import math
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from pydantic import BaseModel, ConfigDict

from .contracts import AudioLoudnessProfile, LoudnessNormalizedVideoArtifact, MediaProbeResult
from .ffprobe import FFprobeAdapter, MediaProbeError, _bounded_stderr
from .process_runner import ProcessRunner


DEFAULT_AUDIO_DURATION_TOLERANCE_SECONDS = 0.1
_FRAME_RATE_TOLERANCE = 0.001


class LoudnessNormalizationError(RuntimeError):
    """Base provider-neutral loudness normalization error."""


class LoudnessSourceNotFoundError(LoudnessNormalizationError):
    """Raised when the input is not an existing local file."""


class MediaAudioMissingError(LoudnessNormalizationError):
    """Raised when an input or normalized output contains no audio."""


class LoudnessAnalysisError(LoudnessNormalizationError):
    """Raised when the FFmpeg analysis pass fails."""


class MalformedLoudnessAnalysisError(LoudnessNormalizationError):
    """Raised when measured loudnorm values are missing or invalid."""


class LoudnessFFmpegError(LoudnessNormalizationError):
    """Raised when the normalization FFmpeg pass fails."""


class EmptyLoudnessOutputError(LoudnessNormalizationError):
    """Raised when normalization produces no media bytes."""


class LoudnessMediaMismatchError(LoudnessNormalizationError):
    """Raised when video properties change during audio normalization."""


class LoudnessDurationMismatchError(LoudnessNormalizationError):
    """Raised when output duration changes beyond the explicit tolerance."""


class LoudnessDestinationExistsError(LoudnessNormalizationError):
    """Raised when publication would overwrite without authorization."""


class _LoudnessMeasurements(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    integrated_lufs: float
    true_peak_db: float
    loudness_range_lu: float
    threshold_lufs: float
    target_offset_db: float


class FFmpegLoudnessNormalizer:
    def __init__(
        self,
        runner: ProcessRunner,
        probe: FFprobeAdapter,
        *,
        executable: str = "ffmpeg",
        audio_codec: str = "aac",
        timeout_seconds: float | None = 120,
        duration_tolerance_seconds: float = DEFAULT_AUDIO_DURATION_TOLERANCE_SECONDS,
    ) -> None:
        if not audio_codec:
            raise ValueError("Audio codec must not be empty.")
        if duration_tolerance_seconds < 0:
            raise ValueError("Duration tolerance must not be negative.")
        self._runner = runner
        self._probe = probe
        self._executable = executable
        self._audio_codec = audio_codec
        self._timeout_seconds = timeout_seconds
        self._duration_tolerance_seconds = duration_tolerance_seconds

    def normalize_loudness(
        self,
        source: Path,
        destination: Path,
        profile: AudioLoudnessProfile,
        *,
        overwrite: bool = False,
    ) -> LoudnessNormalizedVideoArtifact:
        source = Path(source)
        destination = Path(destination)
        if not source.is_file():
            raise LoudnessSourceNotFoundError("The loudness source file does not exist.")
        if destination.exists() and not overwrite:
            raise LoudnessDestinationExistsError("The loudness destination already exists.")
        source_media = self._probe.probe_video(source)
        if not source_media.has_audio:
            raise MediaAudioMissingError("The source video contains no audio stream.")

        measurements = self._analyze(source, profile)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(mode="wb", dir=destination.parent, prefix=f".{destination.stem}.", suffix=f".part{destination.suffix}", delete=False) as temporary:
                temporary_path = Path(temporary.name)
            args = self._normalization_args(source, temporary_path, profile, measurements)
            try:
                result = self._runner.run(args, timeout_seconds=self._timeout_seconds)
            except Exception as error:
                raise LoudnessFFmpegError("ffmpeg loudness normalization could not be executed.") from error
            if result.exit_code != 0:
                detail = _diagnostic_detail(result.stderr)
                raise LoudnessFFmpegError(f"ffmpeg loudness normalization failed with exit code {result.exit_code}{detail}.")
            byte_size = temporary_path.stat().st_size
            if byte_size <= 0:
                raise EmptyLoudnessOutputError("ffmpeg loudness normalization produced an empty output.")
            with temporary_path.open("r+b") as output:
                os.fsync(output.fileno())
            sha256 = _sha256(temporary_path)
            try:
                output_media = self._probe.probe_video(temporary_path)
            except MediaProbeError as error:
                raise LoudnessMediaMismatchError("Loudness-normalized output could not be inspected safely.") from error
            self._validate_output(source_media, output_media)
            if destination.exists() and not overwrite:
                raise LoudnessDestinationExistsError("The loudness destination already exists.")
            os.replace(temporary_path, destination)
            temporary_path = None
            return LoudnessNormalizedVideoArtifact(local_path=destination, byte_size=byte_size, sha256=sha256, media_info=output_media.model_copy(update={"local_path": destination}))
        except LoudnessNormalizationError:
            raise
        except OSError as error:
            raise LoudnessNormalizationError("Loudness-normalized video could not be published atomically.") from error
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _analyze(self, source: Path, profile: AudioLoudnessProfile) -> _LoudnessMeasurements:
        args = [self._executable, "-hide_banner", "-nostats", "-i", str(source), "-map", "0:a:0", "-af", _analysis_filter(profile), "-f", "null", "-"]
        try:
            result = self._runner.run(args, timeout_seconds=self._timeout_seconds)
        except Exception as error:
            raise LoudnessAnalysisError("ffmpeg loudness analysis could not be executed.") from error
        if result.exit_code != 0:
            detail = _diagnostic_detail(result.stderr)
            raise LoudnessAnalysisError(f"ffmpeg loudness analysis failed with exit code {result.exit_code}{detail}.")
        return _parse_measurements(result.stderr)

    def _normalization_args(self, source: Path, destination: Path, profile: AudioLoudnessProfile, measured: _LoudnessMeasurements) -> list[str]:
        return [self._executable, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy", "-c:a", self._audio_codec, "-af", _normalization_filter(profile, measured), str(destination)]

    def _validate_output(self, source: MediaProbeResult, output: MediaProbeResult) -> None:
        if not output.has_audio:
            raise MediaAudioMissingError("The loudness-normalized output contains no audio stream.")
        if (output.width, output.height) != (source.width, source.height) or output.video_codec != source.video_codec:
            raise LoudnessMediaMismatchError("Video properties changed during loudness normalization.")
        if abs(output.frame_rate - source.frame_rate) > _FRAME_RATE_TOLERANCE:
            raise LoudnessMediaMismatchError("Video frame rate changed during loudness normalization.")
        if abs(output.duration_seconds - source.duration_seconds) > self._duration_tolerance_seconds:
            raise LoudnessDurationMismatchError("Loudness-normalized duration is outside the configured tolerance.")


def _analysis_filter(profile: AudioLoudnessProfile) -> str:
    return f"loudnorm=I={_number(profile.integrated_lufs)}:LRA={_number(profile.loudness_range_lu)}:TP={_number(profile.true_peak_db)}:print_format=json"


def _normalization_filter(profile: AudioLoudnessProfile, measured: _LoudnessMeasurements) -> str:
    return ":".join([_analysis_filter(profile).removesuffix(":print_format=json"), f"measured_I={_number(measured.integrated_lufs)}", f"measured_TP={_number(measured.true_peak_db)}", f"measured_LRA={_number(measured.loudness_range_lu)}", f"measured_thresh={_number(measured.threshold_lufs)}", f"offset={_number(measured.target_offset_db)}", "linear=true", "print_format=summary"])


def _parse_measurements(stderr: str) -> _LoudnessMeasurements:
    decoder = json.JSONDecoder()
    payload: dict[str, Any] | None = None
    for index, character in enumerate(stderr):
        if character != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(stderr[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and "input_i" in candidate:
            payload = candidate
    if payload is None:
        raise MalformedLoudnessAnalysisError("ffmpeg loudness analysis was malformed.")
    mapping = {"integrated_lufs": "input_i", "true_peak_db": "input_tp", "loudness_range_lu": "input_lra", "threshold_lufs": "input_thresh", "target_offset_db": "target_offset"}
    try:
        values = {target: _finite_number(payload[source]) for target, source in mapping.items()}
        return _LoudnessMeasurements(**values)
    except (KeyError, TypeError, ValueError) as error:
        raise MalformedLoudnessAnalysisError("ffmpeg loudness measurements are incomplete or invalid.") from error


def _finite_number(value: Any) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("measurement is not finite")
    return number


def _diagnostic_detail(stderr: str) -> str:
    sanitized = stderr
    while "{" in sanitized and "}" in sanitized:
        start = sanitized.find("{")
        end = sanitized.find("}", start)
        if end < 0:
            break
        sanitized = sanitized[:start] + "[loudness analysis omitted]" + sanitized[end + 1 :]
    summary = _bounded_stderr(sanitized)
    return f"; stderr: {summary}" if summary else ""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _number(value: float) -> str:
    return str(int(value)) if value.is_integer() else str(value)
