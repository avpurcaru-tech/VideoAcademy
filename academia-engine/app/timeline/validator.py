import math
from pathlib import Path

from app.media import FFprobeAdapter, MediaProbeResult

from .contracts import (
    TimelineTransitionKind,
    ValidatedTimelineScene,
    ValidatedVideoTimeline,
    VideoTimeline,
)
from .resolver import resolve_timeline


TIMELINE_TIME_TOLERANCE_SECONDS = 1e-6


class TimelineMediaValidationError(RuntimeError):
    """Base safe error for read-only media-aware timeline validation."""


class TimelineSourceMissingError(TimelineMediaValidationError):
    """Raised when a resolved scene source does not exist."""


class TimelineSourceNotFileError(TimelineMediaValidationError):
    """Raised when a resolved source is not a regular file."""


class TimelineMediaProbeError(TimelineMediaValidationError):
    """Raised when source metadata cannot be inspected safely."""


class TimelineInvalidSourceDurationError(TimelineMediaValidationError):
    """Raised when source media duration is non-finite or non-positive."""


class TimelineTrimStartOutOfBoundsError(TimelineMediaValidationError):
    """Raised when effective trim start reaches or exceeds source duration."""


class TimelineTrimEndOutOfBoundsError(TimelineMediaValidationError):
    """Raised when effective trim end exceeds source duration."""


class TimelineEmptyEffectiveDurationError(TimelineMediaValidationError):
    """Raised when a scene has no positive effective duration."""


class TimelineTransitionCurrentSceneError(TimelineMediaValidationError):
    """Raised when a transition is not shorter than its current scene."""


class TimelineTransitionNextSceneError(TimelineMediaValidationError):
    """Raised when a transition is not shorter than its next scene."""


class TimelineInvalidTotalDurationError(TimelineMediaValidationError):
    """Raised when overlap subtraction leaves no positive timeline duration."""


class TimelineMediaValidator:
    def __init__(self, probe: FFprobeAdapter) -> None:
        self._probe = probe

    def validate(self, timeline: VideoTimeline) -> ValidatedVideoTimeline:
        resolved = resolve_timeline(timeline)
        probe_cache: dict[Path, MediaProbeResult] = {}
        validated: list[ValidatedTimelineScene] = []

        for scene in resolved.ordered_scenes:
            source = scene.source_path
            if not source.exists():
                raise TimelineSourceMissingError(
                    f"Timeline {resolved.timeline_id} scene {scene.scene_id} source is missing: {source}"
                )
            if not source.is_file():
                raise TimelineSourceNotFileError(
                    f"Timeline {resolved.timeline_id} scene {scene.scene_id} source is not a regular file: {source}"
                )
            cache_key = source.resolve()
            if cache_key not in probe_cache:
                try:
                    probe_cache[cache_key] = self._probe.probe_video(source)
                except Exception as error:
                    raise TimelineMediaProbeError(
                        f"Timeline {resolved.timeline_id} scene {scene.scene_id} media probe failed: {source}"
                    ) from error
            media = probe_cache[cache_key]
            self._validate_media(resolved.timeline_id, scene.scene_id, source, media)
            start = scene.trim_start_seconds if scene.trim_start_seconds is not None else 0.0
            end = scene.trim_end_seconds if scene.trim_end_seconds is not None else media.duration_seconds
            if start >= media.duration_seconds:
                raise TimelineTrimStartOutOfBoundsError(
                    f"Timeline {resolved.timeline_id} scene {scene.scene_id} trim start reaches or exceeds source duration: {source}"
                )
            if end > media.duration_seconds:
                raise TimelineTrimEndOutOfBoundsError(
                    f"Timeline {resolved.timeline_id} scene {scene.scene_id} trim end exceeds source duration: {source}"
                )
            duration = end - start
            if duration <= TIMELINE_TIME_TOLERANCE_SECONDS:
                raise TimelineEmptyEffectiveDurationError(
                    f"Timeline {resolved.timeline_id} scene {scene.scene_id} has empty effective duration: {source}"
                )
            validated.append(
                ValidatedTimelineScene(
                    scene_id=scene.scene_id,
                    source_path=source,
                    order=scene.order,
                    source_media_info=media,
                    effective_start_seconds=start,
                    effective_end_seconds=end,
                    effective_duration_seconds=duration,
                    transition_to_next=scene.transition_to_next,
                )
            )

        overlap = self._validate_transitions(resolved.timeline_id, validated)
        total = sum(scene.effective_duration_seconds for scene in validated) - overlap
        if not math.isfinite(total) or total <= TIMELINE_TIME_TOLERANCE_SECONDS:
            raise TimelineInvalidTotalDurationError(
                f"Timeline {resolved.timeline_id} total duration is not positive."
            )
        return ValidatedVideoTimeline(
            timeline_id=resolved.timeline_id,
            scenes=tuple(validated),
            destination=resolved.destination,
            workspace=resolved.workspace,
            source_count=len(validated),
            total_duration_seconds=total,
        )

    @staticmethod
    def _validate_media(
        timeline_id: str,
        scene_id: str,
        source: Path,
        media: MediaProbeResult,
    ) -> None:
        duration = media.duration_seconds
        if not math.isfinite(duration) or duration <= 0:
            raise TimelineInvalidSourceDurationError(
                f"Timeline {timeline_id} scene {scene_id} has invalid source duration: {source}"
            )
        if media.width <= 0 or media.height <= 0 or media.frame_rate <= 0:
            raise TimelineMediaProbeError(
                f"Timeline {timeline_id} scene {scene_id} has invalid video metadata: {source}"
            )

    @staticmethod
    def _validate_transitions(
        timeline_id: str,
        scenes: list[ValidatedTimelineScene],
    ) -> float:
        overlap = 0.0
        for index, current in enumerate(scenes[:-1]):
            transition = current.transition_to_next
            if transition is None or transition.kind == TimelineTransitionKind.CUT:
                continue
            duration = transition.duration_seconds or 0.0
            if duration + TIMELINE_TIME_TOLERANCE_SECONDS >= current.effective_duration_seconds:
                raise TimelineTransitionCurrentSceneError(
                    f"Timeline {timeline_id} scene {current.scene_id} transition is not shorter than the current scene."
                )
            next_scene = scenes[index + 1]
            if duration + TIMELINE_TIME_TOLERANCE_SECONDS >= next_scene.effective_duration_seconds:
                raise TimelineTransitionNextSceneError(
                    f"Timeline {timeline_id} scene {current.scene_id} transition is not shorter than next scene {next_scene.scene_id}."
                )
            overlap += duration
        return overlap
