import hashlib
import math
import os
import re
from pathlib import Path
from tempfile import NamedTemporaryFile

from pydantic import BaseModel, ConfigDict, Field

from app.media import FFprobeAdapter, MediaProbeResult, ProcessRunner, VideoNormalizationProfile

from .ffmpeg_compiler import FFmpegTimelineCompilerError, compile_ffmpeg_timeline
from .render_plan import TimelineRenderPlan


DEFAULT_TIMELINE_RENDER_DURATION_TOLERANCE_SECONDS = 0.25
TIMELINE_RENDER_FRAME_RATE_TOLERANCE = 0.001
_DIAGNOSTIC_LIMIT = 500


class TimelineRendererError(RuntimeError):
    """Base safe provider-neutral timeline rendering error."""


class TimelineRenderDestinationConflictError(TimelineRendererError):
    """Raised before execution when final output exists without overwrite authorization."""


class TimelineRenderTemporaryOutputError(TimelineRendererError):
    """Raised when an isolated same-directory output path cannot be prepared."""


class TimelineRenderCompilationError(TimelineRendererError):
    """Raised when the semantic plan cannot be compiled safely."""


class TimelineRenderExecutionError(TimelineRendererError):
    """Raised when the single FFmpeg process fails."""


class TimelineRenderedOutputMissingError(TimelineRendererError):
    """Raised when FFmpeg succeeds without producing a regular output file."""


class TimelineRenderedOutputEmptyError(TimelineRendererError):
    """Raised when FFmpeg produces an empty output file."""


class TimelineRenderProbeError(TimelineRendererError):
    """Raised when rendered output cannot be probed safely."""


class TimelineRenderResolutionMismatchError(TimelineRendererError):
    """Raised when rendered dimensions differ from the requested profile."""


class TimelineRenderFrameRateMismatchError(TimelineRendererError):
    """Raised when rendered frame rate differs from the requested profile."""


class TimelineRenderCodecMismatchError(TimelineRendererError):
    """Raised when rendered video codec is incompatible with the requested encoder."""


class TimelineRenderAudioMismatchError(TimelineRendererError):
    """Raised when rendered audio presence differs from the compiled command."""


class TimelineRenderDurationMismatchError(TimelineRendererError):
    """Raised when encoded duration differs beyond the practical render tolerance."""


class TimelineRenderPublicationError(TimelineRendererError):
    """Raised when validated output cannot be atomically published."""


class RenderedTimelineArtifact(BaseModel):
    """Durable final render metadata without compiler or temporary details."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    timeline_id: str
    local_path: Path
    byte_size: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    media_info: MediaProbeResult
    source_count: int = Field(ge=2)
    transition_count: int = Field(ge=0)


class FFmpegTimelineRenderer:
    def __init__(
        self,
        runner: ProcessRunner,
        probe: FFprobeAdapter,
        *,
        timeout_seconds: float | None = None,
        duration_tolerance_seconds: float = DEFAULT_TIMELINE_RENDER_DURATION_TOLERANCE_SECONDS,
    ) -> None:
        if duration_tolerance_seconds < 0:
            raise ValueError("Render duration tolerance must not be negative.")
        self._runner = runner
        self._probe = probe
        self._timeout_seconds = timeout_seconds
        self._duration_tolerance_seconds = duration_tolerance_seconds

    def render(
        self,
        plan: TimelineRenderPlan,
        profile: VideoNormalizationProfile | None = None,
        overwrite: bool = False,
    ) -> RenderedTimelineArtifact:
        selected_profile = profile or VideoNormalizationProfile.academia_default()
        destination = plan.destination
        if destination.exists() and not overwrite:
            raise TimelineRenderDestinationConflictError(
                "The timeline render destination already exists."
            )
        temporary_path = self._prepare_temporary_path(destination)
        try:
            try:
                command = compile_ffmpeg_timeline(
                    plan,
                    profile=selected_profile,
                    overwrite=overwrite,
                    output_path=temporary_path,
                )
            except FFmpegTimelineCompilerError as error:
                raise TimelineRenderCompilationError(
                    "The timeline render plan could not be compiled safely."
                ) from error
            try:
                result = self._runner.run(
                    command.args,
                    timeout_seconds=self._timeout_seconds,
                )
            except Exception as error:
                raise TimelineRenderExecutionError("ffmpeg timeline render could not be executed.") from error
            if result.exit_code != 0:
                detail = _diagnostic_detail(result.stderr)
                raise TimelineRenderExecutionError(
                    f"ffmpeg timeline render failed with exit code {result.exit_code}{detail}."
                )
            if not temporary_path.exists() or not temporary_path.is_file():
                raise TimelineRenderedOutputMissingError(
                    "ffmpeg timeline render did not produce a regular output file."
                )
            byte_size = temporary_path.stat().st_size
            if byte_size <= 0:
                raise TimelineRenderedOutputEmptyError(
                    "ffmpeg timeline render produced an empty output file."
                )
            try:
                with temporary_path.open("r+b") as output:
                    os.fsync(output.fileno())
                sha256 = _sha256(temporary_path)
            except OSError as error:
                raise TimelineRenderTemporaryOutputError(
                    "Rendered temporary output could not be synchronized safely."
                ) from error
            try:
                media_info = self._probe.probe_video(temporary_path)
            except Exception as error:
                raise TimelineRenderProbeError(
                    "Rendered timeline output could not be probed safely."
                ) from error
            self._validate_output(
                plan,
                command.has_audio_output,
                media_info,
                selected_profile,
            )
            if destination.exists() and not overwrite:
                raise TimelineRenderDestinationConflictError(
                    "The timeline render destination appeared before atomic publication."
                )
            try:
                os.replace(temporary_path, destination)
            except OSError as error:
                raise TimelineRenderPublicationError(
                    "Validated timeline output could not be published atomically."
                ) from error
            temporary_path = None
            durable_media = media_info.model_copy(update={"local_path": destination})
            return RenderedTimelineArtifact(
                timeline_id=plan.timeline_id,
                local_path=destination,
                byte_size=byte_size,
                sha256=sha256,
                media_info=durable_media,
                source_count=len(plan.scenes),
                transition_count=len(plan.transitions),
            )
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _prepare_temporary_path(destination: Path) -> Path:
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(
                mode="wb",
                dir=destination.parent,
                prefix=f".{destination.stem}.",
                suffix=f".part{destination.suffix}",
                delete=False,
            ) as temporary:
                path = Path(temporary.name)
            path.unlink()
            return path
        except OSError as error:
            raise TimelineRenderTemporaryOutputError(
                "A same-directory timeline render path could not be prepared."
            ) from error

    def _validate_output(
        self,
        plan: TimelineRenderPlan,
        expected_audio: bool,
        media: MediaProbeResult,
        profile: VideoNormalizationProfile,
    ) -> None:
        if (media.width, media.height) != (profile.width, profile.height):
            raise TimelineRenderResolutionMismatchError(
                "Rendered timeline resolution does not match the requested profile."
            )
        if not math.isclose(
            media.frame_rate,
            profile.frame_rate,
            rel_tol=0.0,
            abs_tol=TIMELINE_RENDER_FRAME_RATE_TOLERANCE,
        ):
            raise TimelineRenderFrameRateMismatchError(
                "Rendered timeline frame rate does not match the requested profile."
            )
        if media.video_codec.lower() != _expected_probe_codec(profile.video_codec):
            raise TimelineRenderCodecMismatchError(
                "Rendered timeline video codec does not match the requested profile."
            )
        if media.has_audio is not expected_audio:
            raise TimelineRenderAudioMismatchError(
                "Rendered timeline audio presence does not match the compiled command."
            )
        if abs(media.duration_seconds - plan.expected_duration_seconds) > self._duration_tolerance_seconds:
            raise TimelineRenderDurationMismatchError(
                "Rendered timeline duration is outside the configured tolerance."
            )


def _expected_probe_codec(encoder: str) -> str:
    mapping = {
        "libx264": "h264",
        "libx265": "hevc",
    }
    return mapping.get(encoder.lower(), encoder.lower())


def _diagnostic_detail(stderr: str) -> str:
    summary = " ".join(stderr.split())
    summary = re.sub(
        r"(?i)(authorization|api[_-]?key|token|secret)\s*[:=]?\s*\S+",
        r"\1=[redacted]",
        summary,
    )[:_DIAGNOSTIC_LIMIT]
    return f"; stderr: {summary}" if summary else ""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
