import math
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.media import VideoNormalizationProfile

from .contracts import TimelineTransitionKind
from .render_plan import RenderScene, RenderTransition, TimelineRenderPlan
from .validator import TIMELINE_TIME_TOLERANCE_SECONDS


class FFmpegTimelineCompilerError(RuntimeError):
    """Base safe error for deterministic FFmpeg command compilation."""


class FFmpegTimelineInputError(FFmpegTimelineCompilerError):
    """Raised when render-plan input order or scene timing is invalid."""


class FFmpegTimelineTransitionError(FFmpegTimelineCompilerError):
    """Raised when transition references or overlap timing are inconsistent."""


class FFmpegTimelineAudioCompatibilityError(FFmpegTimelineCompilerError):
    """Raised when scenes mix audio presence under the strict compiler policy."""


class FFmpegTimelineDurationError(FFmpegTimelineCompilerError):
    """Raised when final timing differs from the authoritative expected duration."""


class FFmpegTimelineCommand(BaseModel):
    """Argv-only FFmpeg execution specification; this model executes nothing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    args: tuple[str, ...]
    expected_output_path: Path
    filter_complex: str
    input_count: int = Field(ge=2)
    has_audio_output: bool
    transition_count: int = Field(ge=0)


def compile_ffmpeg_timeline(
    plan: TimelineRenderPlan,
    profile: VideoNormalizationProfile | None = None,
    overwrite: bool = False,
) -> FFmpegTimelineCommand:
    """Compile semantic plan timing into deterministic argv and filter graph."""
    selected_profile = profile or VideoNormalizationProfile.academia_default()
    scenes = plan.scenes
    _validate_scenes(plan)
    transitions_by_boundary = _validate_and_index_transitions(plan)
    audio_values = {scene.has_audio for scene in scenes}
    if len(audio_values) != 1:
        raise FFmpegTimelineAudioCompatibilityError(
            "FFmpeg timeline compilation requires all scenes to consistently contain audio or no audio."
        )
    has_audio = scenes[0].has_audio

    filters: list[str] = []
    for scene in scenes:
        index = scene.input_index
        start = _format_number(scene.source_start_seconds)
        end = _format_number(scene.source_end_seconds)
        filters.append(
            f"[{index}:v:0]trim=start={start}:end={end},setpts=PTS-STARTPTS[v{index}]"
        )
        if has_audio:
            filters.append(
                f"[{index}:a:0]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[a{index}]"
            )

    if not transitions_by_boundary:
        video_inputs = "".join(f"[v{scene.input_index}]" for scene in scenes)
        filters.append(f"{video_inputs}concat=n={len(scenes)}:v=1:a=0[vout]")
        if has_audio:
            audio_inputs = "".join(f"[a{scene.input_index}]" for scene in scenes)
            filters.append(f"{audio_inputs}concat=n={len(scenes)}:v=0:a=1[aout]")
    else:
        _compile_mixed_chain(scenes, transitions_by_boundary, has_audio, filters)

    filter_complex = ";".join(filters)
    args: list[str] = ["ffmpeg", "-y" if overwrite else "-n"]
    for scene in scenes:
        args.extend(["-i", str(scene.source_path)])
    args.extend(["-filter_complex", filter_complex, "-map", "[vout]"])
    if has_audio:
        args.extend(["-map", "[aout]"])
    args.extend(
        [
            "-c:v",
            selected_profile.video_codec,
            "-pix_fmt",
            selected_profile.pixel_format,
            "-r",
            _format_number(selected_profile.frame_rate),
        ]
    )
    if has_audio:
        args.extend(["-c:a", selected_profile.audio_codec])
    args.append(str(plan.destination))
    return FFmpegTimelineCommand(
        args=tuple(args),
        expected_output_path=plan.destination,
        filter_complex=filter_complex,
        input_count=len(scenes),
        has_audio_output=has_audio,
        transition_count=len(plan.transitions),
    )


def _compile_mixed_chain(
    scenes: tuple[RenderScene, ...],
    transitions: dict[int, RenderTransition],
    has_audio: bool,
    filters: list[str],
) -> None:
    current_video = "v0"
    current_audio = "a0"
    last_boundary = len(scenes) - 1
    for boundary in range(last_boundary):
        next_index = boundary + 1
        video_output = "vout" if next_index == last_boundary else f"vx{next_index}"
        audio_output = "aout" if next_index == last_boundary else f"ax{next_index}"
        transition = transitions.get(boundary)
        if transition is None:
            filters.append(
                f"[{current_video}][v{next_index}]concat=n=2:v=1:a=0[{video_output}]"
            )
            if has_audio:
                filters.append(
                    f"[{current_audio}][a{next_index}]concat=n=2:v=0:a=1[{audio_output}]"
                )
        else:
            ffmpeg_kind = _xfade_kind(transition.kind)
            duration = _format_number(transition.duration_seconds)
            offset = _format_number(transition.start_seconds)
            filters.append(
                f"[{current_video}][v{next_index}]xfade=transition={ffmpeg_kind}:duration={duration}:offset={offset}[{video_output}]"
            )
            if has_audio:
                filters.append(
                    f"[{current_audio}][a{next_index}]acrossfade=d={duration}:c1=tri:c2=tri[{audio_output}]"
                )
        current_video = video_output
        if has_audio:
            current_audio = audio_output


def _validate_scenes(plan: TimelineRenderPlan) -> None:
    scenes = plan.scenes
    if len(scenes) < 2:
        raise FFmpegTimelineInputError("FFmpeg timeline compilation requires at least two scenes.")
    indexes = [scene.input_index for scene in scenes]
    if indexes != list(range(len(scenes))) or len(set(indexes)) != len(indexes):
        raise FFmpegTimelineInputError(
            "Render scene tuple order must match contiguous input indexes beginning at zero."
        )
    scene_ids = [scene.scene_id for scene in scenes]
    if len(scene_ids) != len(set(scene_ids)):
        raise FFmpegTimelineInputError("Render scene IDs must be unique for transition references.")
    for scene in scenes:
        values = (
            scene.source_start_seconds,
            scene.source_end_seconds,
            scene.effective_duration_seconds,
            scene.output_start_seconds,
            scene.output_end_seconds,
        )
        if not all(math.isfinite(value) for value in values):
            raise FFmpegTimelineInputError(f"Render scene {scene.scene_id} has non-finite timing.")
        if (
            scene.source_start_seconds < 0
            or scene.source_end_seconds <= scene.source_start_seconds
            or scene.effective_duration_seconds <= 0
            or scene.output_start_seconds < 0
            or scene.output_end_seconds <= scene.output_start_seconds
            or not _same_time(
                scene.source_end_seconds - scene.source_start_seconds,
                scene.effective_duration_seconds,
            )
            or not _same_time(
                scene.output_end_seconds - scene.output_start_seconds,
                scene.effective_duration_seconds,
            )
        ):
            raise FFmpegTimelineInputError(f"Render scene {scene.scene_id} has invalid timing.")
    if not math.isfinite(plan.expected_duration_seconds) or plan.expected_duration_seconds <= 0:
        raise FFmpegTimelineDurationError("Expected timeline duration must be positive.")
    if not _same_time(scenes[-1].output_end_seconds, plan.expected_duration_seconds):
        raise FFmpegTimelineDurationError(
            "Final render scene does not match expected timeline duration."
        )


def _validate_and_index_transitions(plan: TimelineRenderPlan) -> dict[int, RenderTransition]:
    boundaries = {
        (plan.scenes[index].scene_id, plan.scenes[index + 1].scene_id): index
        for index in range(len(plan.scenes) - 1)
    }
    result: dict[int, RenderTransition] = {}
    last_boundary = -1
    for transition in plan.transitions:
        key = (transition.from_scene_id, transition.to_scene_id)
        if key not in boundaries:
            raise FFmpegTimelineTransitionError(
                "Render transition does not reference adjacent scenes."
            )
        boundary = boundaries[key]
        if boundary in result or boundary <= last_boundary:
            raise FFmpegTimelineTransitionError(
                "Render transitions are duplicated or not in deterministic boundary order."
            )
        if transition.kind not in {
            TimelineTransitionKind.FADE,
            TimelineTransitionKind.DISSOLVE,
        }:
            raise FFmpegTimelineTransitionError(
                "Only materialized fade or dissolve transitions are supported."
            )
        current = plan.scenes[boundary]
        following = plan.scenes[boundary + 1]
        if (
            transition.duration_seconds <= 0
            or not _same_time(
                transition.end_seconds - transition.start_seconds,
                transition.duration_seconds,
            )
            or not _same_time(transition.end_seconds, current.output_end_seconds)
            or not _same_time(transition.start_seconds, following.output_start_seconds)
        ):
            raise FFmpegTimelineTransitionError(
                "Render transition timing does not match its adjacent scene overlap."
            )
        result[boundary] = transition
        last_boundary = boundary

    for boundary in range(len(plan.scenes) - 1):
        current = plan.scenes[boundary]
        following = plan.scenes[boundary + 1]
        overlaps = following.output_start_seconds < (
            current.output_end_seconds - TIMELINE_TIME_TOLERANCE_SECONDS
        )
        adjacent = _same_time(following.output_start_seconds, current.output_end_seconds)
        if overlaps != (boundary in result) or (not overlaps and not adjacent):
            raise FFmpegTimelineTransitionError(
                "Scene overlap and materialized transition boundaries are inconsistent."
            )
    return result


def _xfade_kind(kind: TimelineTransitionKind) -> str:
    mapping = {
        TimelineTransitionKind.FADE: "fade",
        TimelineTransitionKind.DISSOLVE: "dissolve",
    }
    try:
        return mapping[kind]
    except KeyError as error:
        raise FFmpegTimelineTransitionError("Unsupported semantic transition kind.") from error


def _format_number(value: float) -> str:
    if not math.isfinite(value):
        raise FFmpegTimelineCompilerError("FFmpeg timeline number must be finite.")
    quantized = Decimal(str(value)).quantize(
        Decimal("0.000000001"),
        rounding=ROUND_HALF_UP,
    )
    formatted = format(quantized, "f").rstrip("0").rstrip(".")
    return "0" if formatted in {"", "-0"} else formatted


def _same_time(left: float, right: float) -> bool:
    return math.isclose(
        left,
        right,
        rel_tol=0.0,
        abs_tol=TIMELINE_TIME_TOLERANCE_SECONDS,
    )
