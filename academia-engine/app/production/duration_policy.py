from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


class SceneDurationPlanningError(RuntimeError):
    pass


@dataclass(frozen=True)
class SceneDurationPolicy:
    """Provider-neutral conversion from semantic runtime to fixed generation clips.

    Scene count uses nearest-clip rounding (half rounds up). Every execution clip
    has exactly ``execution_duration_seconds``; semantic scene pacing remains on
    Episode and DirectorPlan objects.
    """

    execution_duration_seconds: int
    minimum_scene_count: int = 2
    maximum_scene_count: int = 12

    def __post_init__(self) -> None:
        if self.execution_duration_seconds <= 0:
            raise ValueError("Execution scene duration must be positive.")
        if self.minimum_scene_count <= 0 or self.maximum_scene_count < self.minimum_scene_count:
            raise ValueError("Scene count bounds are invalid.")

    def scene_count(self, target_duration_seconds: float) -> int:
        if target_duration_seconds <= 0:
            raise SceneDurationPlanningError("Target duration must be positive.")
        count = int((Decimal(str(target_duration_seconds)) / Decimal(self.execution_duration_seconds)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP))
        if count < self.minimum_scene_count or count > self.maximum_scene_count:
            raise SceneDurationPlanningError(
                "Target duration cannot be represented within the supported scene-count bounds."
            )
        return count

    def apply_execution_duration(self, video_request):
        return video_request.model_copy(update={"duration_seconds": self.execution_duration_seconds})
