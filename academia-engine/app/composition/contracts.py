import json
import ntpath
import re
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from app.media import MediaProbeResult


_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
SceneOrder = Annotated[StrictInt, Field(ge=0)]


class VideoCompositionScene(BaseModel):
    """One durable local scene reference with explicit composition order."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scene_id: str = Field(min_length=1, max_length=200)
    source_path: Path
    order: SceneOrder

    @field_validator("scene_id")
    @classmethod
    def scene_id_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Scene ID must not be blank.")
        return value

    @field_validator("source_path", mode="before")
    @classmethod
    def source_must_be_local(cls, value: Any) -> Any:
        _validate_local_path(value, "Scene source path")
        return value


class VideoCompositionOutput(BaseModel):
    """Explicit local final destination and isolated workspace root."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    destination: Path
    workspace: Path

    @field_validator("destination", "workspace", mode="before")
    @classmethod
    def output_paths_must_be_local(cls, value: Any) -> Any:
        _validate_local_path(value, "Composition output path")
        return value

    @model_validator(mode="after")
    def destination_and_workspace_must_differ(self) -> "VideoCompositionOutput":
        if _normalized_path(self.destination) == _normalized_path(self.workspace):
            raise ValueError("Composition destination and workspace must be different paths.")
        return self


class VideoCompositionManifest(BaseModel):
    """Provider-neutral durable description of ordered local video inputs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    composition_id: str = Field(min_length=1, max_length=200)
    scenes: tuple[VideoCompositionScene, ...]
    output: VideoCompositionOutput

    @field_validator("composition_id")
    @classmethod
    def composition_id_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Composition ID must not be blank.")
        return value

    @model_validator(mode="after")
    def scenes_must_be_sufficient_and_unique(self) -> "VideoCompositionManifest":
        if len(self.scenes) < 2:
            raise ValueError("A composition manifest requires at least two scenes.")
        scene_ids = [scene.scene_id for scene in self.scenes]
        if len(set(scene_ids)) != len(scene_ids):
            raise ValueError("Composition scene IDs must be unique.")
        orders = [scene.order for scene in self.scenes]
        if len(set(orders)) != len(orders):
            raise ValueError("Composition scene order values must be unique.")
        return self

    def to_json(self) -> str:
        """Serialize using stable contract field names and scene representation."""
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, value: str) -> "VideoCompositionManifest":
        return cls.model_validate_json(value)


class ResolvedVideoComposition(BaseModel):
    """Pure deterministic resolution of a validated composition manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    composition_id: str
    ordered_sources: tuple[Path, ...]
    destination: Path
    workspace: Path
    source_count: int = Field(ge=2)


class CompositionExecutionResult(BaseModel):
    """Durable final composition metadata with no execution intermediates."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    composition_id: str = Field(min_length=1)
    local_path: Path
    byte_size: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    media_info: MediaProbeResult
    source_count: int = Field(ge=2)


def _validate_local_path(value: Any, field_name: str) -> None:
    if not isinstance(value, (str, Path)):
        raise ValueError(f"{field_name} must be an explicit local path.")
    raw = str(value).strip()
    if not raw or raw in {".", ".."}:
        raise ValueError(f"{field_name} must be explicit.")
    normalized = raw.replace("\\", "/")
    if not _WINDOWS_DRIVE.match(raw) and _URI_SCHEME.match(raw):
        raise ValueError(f"{field_name} must not contain a URI scheme.")
    if normalized.lower().startswith(("http:/", "https:/", "//")):
        raise ValueError(f"{field_name} must be local, not remote.")
    if "?" in raw or "#" in raw:
        raise ValueError(f"{field_name} must not contain URL query or fragment data.")


def _normalized_path(path: Path) -> str:
    return ntpath.normcase(ntpath.abspath(str(path)))
