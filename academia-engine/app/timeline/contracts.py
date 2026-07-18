import json
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from app.composition.paths import normalized_local_path, validate_local_path
from app.media import MediaProbeResult


TimelineOrder = Annotated[StrictInt, Field(ge=0)]


class TimelineTransitionKind(str, Enum):
    CUT = "cut"
    FADE = "fade"
    DISSOLVE = "dissolve"


class TimelineTransition(BaseModel):
    """Semantic transition intent with no renderer or filter expression."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    kind: TimelineTransitionKind
    duration_seconds: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def duration_must_match_kind(self) -> "TimelineTransition":
        if self.kind == TimelineTransitionKind.CUT:
            if self.duration_seconds not in (None, 0):
                raise ValueError("A cut transition cannot have positive duration.")
        elif self.duration_seconds is None or self.duration_seconds <= 0:
            raise ValueError("Fade and dissolve transitions require positive duration.")
        return self


class TimelineScene(BaseModel):
    """One ordered local scene with duration-independent temporal intent."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    scene_id: str = Field(min_length=1, max_length=200)
    source_path: Path
    order: TimelineOrder
    trim_start_seconds: float | None = Field(default=None, ge=0)
    trim_end_seconds: float | None = Field(default=None, gt=0)
    transition_to_next: TimelineTransition | None = None

    @field_validator("scene_id")
    @classmethod
    def scene_id_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Timeline scene ID must not be blank.")
        return value

    @field_validator("source_path", mode="before")
    @classmethod
    def source_must_be_local(cls, value: Any) -> Any:
        validate_local_path(value, "Timeline scene source path")
        return value

    @model_validator(mode="after")
    def trim_range_must_be_ordered(self) -> "TimelineScene":
        if (
            self.trim_start_seconds is not None
            and self.trim_end_seconds is not None
            and self.trim_end_seconds <= self.trim_start_seconds
        ):
            raise ValueError("Timeline trim end must be greater than trim start.")
        return self


class TimelineOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    destination: Path
    workspace: Path

    @field_validator("destination", "workspace", mode="before")
    @classmethod
    def paths_must_be_local(cls, value: Any) -> Any:
        validate_local_path(value, "Timeline output path")
        return value

    @model_validator(mode="after")
    def output_paths_must_differ(self) -> "TimelineOutput":
        if normalized_local_path(self.destination) == normalized_local_path(self.workspace):
            raise ValueError("Timeline destination and workspace must be different paths.")
        return self


class VideoTimeline(BaseModel):
    """Provider-neutral semantic timeline contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    timeline_id: str = Field(min_length=1, max_length=200)
    scenes: tuple[TimelineScene, ...]
    output: TimelineOutput

    @field_validator("timeline_id")
    @classmethod
    def timeline_id_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Timeline ID must not be blank.")
        return value

    @model_validator(mode="after")
    def scene_set_must_be_valid(self) -> "VideoTimeline":
        if len(self.scenes) < 2:
            raise ValueError("A video timeline requires at least two scenes.")
        ids = [scene.scene_id for scene in self.scenes]
        if len(ids) != len(set(ids)):
            raise ValueError("Timeline scene IDs must be unique.")
        orders = [scene.order for scene in self.scenes]
        if len(orders) != len(set(orders)):
            raise ValueError("Timeline scene order values must be unique.")
        last = max(self.scenes, key=lambda scene: scene.order)
        if last.transition_to_next is not None:
            raise ValueError("The last resolved timeline scene cannot define a transition.")
        return self

    def to_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, value: str) -> "VideoTimeline":
        return cls.model_validate_json(value)


class ResolvedTimelineScene(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scene_id: str
    source_path: Path
    order: int = Field(ge=0)
    trim_start_seconds: float | None
    trim_end_seconds: float | None
    transition_to_next: TimelineTransition | None


class ResolvedVideoTimeline(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    timeline_id: str
    ordered_scenes: tuple[ResolvedTimelineScene, ...]
    destination: Path
    workspace: Path
    source_count: int = Field(ge=2)


class ValidatedTimelineScene(BaseModel):
    """One temporally feasible scene backed by normalized probe metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    scene_id: str
    source_path: Path
    order: int = Field(ge=0)
    source_media_info: MediaProbeResult
    effective_start_seconds: float = Field(ge=0)
    effective_end_seconds: float = Field(gt=0)
    effective_duration_seconds: float = Field(gt=0)
    transition_to_next: TimelineTransition | None


class ValidatedVideoTimeline(BaseModel):
    """Read-only media-aware timeline validation result."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    timeline_id: str
    scenes: tuple[ValidatedTimelineScene, ...]
    destination: Path
    workspace: Path
    source_count: int = Field(ge=2)
    total_duration_seconds: float = Field(gt=0)
