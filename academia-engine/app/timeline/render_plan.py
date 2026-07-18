import json
import math
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .contracts import (
    TimelineTransitionKind,
    ValidatedTimelineScene,
    ValidatedVideoTimeline,
)
from .validator import TIMELINE_TIME_TOLERANCE_SECONDS


class TimelineRenderPlanError(RuntimeError):
    """Base provider-neutral semantic render-plan error."""


class TimelineRenderPlanInvariantError(TimelineRenderPlanError):
    """Raised when validated scene or overlap timing is internally inconsistent."""


class TimelineRenderPlanDurationError(TimelineRenderPlanError):
    """Raised when calculated final position differs from validated duration."""


class RenderScene(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    scene_id: str
    source_path: Path
    input_index: int = Field(ge=0)
    source_start_seconds: float = Field(ge=0)
    source_end_seconds: float = Field(gt=0)
    effective_duration_seconds: float = Field(gt=0)
    output_start_seconds: float = Field(ge=0)
    output_end_seconds: float = Field(gt=0)
    has_audio: bool


class RenderTransition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    from_scene_id: str
    to_scene_id: str
    kind: TimelineTransitionKind
    duration_seconds: float = Field(gt=0)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)


class TimelineRenderPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    timeline_id: str
    scenes: tuple[RenderScene, ...]
    transitions: tuple[RenderTransition, ...]
    destination: Path
    workspace: Path
    expected_duration_seconds: float = Field(gt=0)

    def to_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, value: str) -> "TimelineRenderPlan":
        return cls.model_validate_json(value)


def build_render_plan(validated: ValidatedVideoTimeline) -> TimelineRenderPlan:
    """Build deterministic semantic timing without filesystem or renderer access."""
    if len(validated.scenes) < 2:
        raise TimelineRenderPlanInvariantError(
            "A render plan requires at least two validated scenes."
        )
    if validated.scenes[-1].transition_to_next is not None:
        raise TimelineRenderPlanInvariantError(
            "The last validated scene cannot define a transition."
        )

    render_scenes: list[RenderScene] = []
    for input_index, scene in enumerate(validated.scenes):
        _validate_validated_scene(scene)
        if input_index == 0:
            output_start = 0.0
        else:
            previous_render = render_scenes[-1]
            previous_transition = validated.scenes[input_index - 1].transition_to_next
            if (
                previous_transition is not None
                and previous_transition.kind != TimelineTransitionKind.CUT
            ):
                transition_duration = previous_transition.duration_seconds or 0.0
                if (
                    transition_duration <= 0
                    or transition_duration + TIMELINE_TIME_TOLERANCE_SECONDS
                    >= validated.scenes[input_index - 1].effective_duration_seconds
                    or transition_duration + TIMELINE_TIME_TOLERANCE_SECONDS
                    >= scene.effective_duration_seconds
                ):
                    raise TimelineRenderPlanInvariantError(
                        f"Transition into scene {scene.scene_id} is incompatible with scene timing."
                    )
                output_start = (
                    previous_render.output_end_seconds
                    - transition_duration
                )
            else:
                output_start = previous_render.output_end_seconds
        output_end = output_start + scene.effective_duration_seconds
        if output_start < 0 or output_end <= output_start:
            raise TimelineRenderPlanInvariantError(
                f"Scene {scene.scene_id} has invalid calculated output timing."
            )
        render_scenes.append(
            RenderScene(
                scene_id=scene.scene_id,
                source_path=scene.source_path,
                input_index=input_index,
                source_start_seconds=scene.effective_start_seconds,
                source_end_seconds=scene.effective_end_seconds,
                effective_duration_seconds=scene.effective_duration_seconds,
                output_start_seconds=output_start,
                output_end_seconds=output_end,
                has_audio=scene.source_media_info.has_audio,
            )
        )

    transitions: list[RenderTransition] = []
    for index, current in enumerate(validated.scenes[:-1]):
        semantic = current.transition_to_next
        if semantic is None or semantic.kind == TimelineTransitionKind.CUT:
            continue
        duration = semantic.duration_seconds or 0.0
        previous = render_scenes[index]
        following = render_scenes[index + 1]
        end = previous.output_end_seconds
        start = end - duration
        if not _same_time(following.output_start_seconds, start):
            raise TimelineRenderPlanInvariantError(
                f"Transition from {current.scene_id} does not begin with the next scene."
            )
        if not _same_time(end, previous.output_end_seconds):
            raise TimelineRenderPlanInvariantError(
                f"Transition from {current.scene_id} does not end with the current scene."
            )
        if duration <= 0 or not _same_time(end - start, duration):
            raise TimelineRenderPlanInvariantError(
                f"Transition from {current.scene_id} has inconsistent duration."
            )
        transitions.append(
            RenderTransition(
                from_scene_id=current.scene_id,
                to_scene_id=validated.scenes[index + 1].scene_id,
                kind=semantic.kind,
                duration_seconds=duration,
                start_seconds=start,
                end_seconds=end,
            )
        )

    indexes = [scene.input_index for scene in render_scenes]
    if indexes != list(range(len(render_scenes))):
        raise TimelineRenderPlanInvariantError("Render input indexes are not contiguous.")
    final_end = render_scenes[-1].output_end_seconds
    if not _same_time(final_end, validated.total_duration_seconds):
        raise TimelineRenderPlanDurationError(
            "Render plan final position does not match validated timeline duration."
        )
    return TimelineRenderPlan(
        timeline_id=validated.timeline_id,
        scenes=tuple(render_scenes),
        transitions=tuple(transitions),
        destination=validated.destination,
        workspace=validated.workspace,
        expected_duration_seconds=validated.total_duration_seconds,
    )


def _validate_validated_scene(scene: ValidatedTimelineScene) -> None:
    values = (
        scene.effective_start_seconds,
        scene.effective_end_seconds,
        scene.effective_duration_seconds,
    )
    if not all(math.isfinite(value) for value in values):
        raise TimelineRenderPlanInvariantError(
            f"Scene {scene.scene_id} contains non-finite timing."
        )
    if (
        scene.effective_start_seconds < 0
        or scene.effective_end_seconds <= scene.effective_start_seconds
        or scene.effective_duration_seconds <= 0
        or not _same_time(
            scene.effective_end_seconds - scene.effective_start_seconds,
            scene.effective_duration_seconds,
        )
    ):
        raise TimelineRenderPlanInvariantError(
            f"Scene {scene.scene_id} contains inconsistent validated timing."
        )


def _same_time(left: float, right: float) -> bool:
    return math.isclose(
        left,
        right,
        rel_tol=0.0,
        abs_tol=TIMELINE_TIME_TOLERANCE_SECONDS,
    )
