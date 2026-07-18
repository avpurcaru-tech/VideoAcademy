import json
import os
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.models import GenerationTaskStatus


class ArtifactRecord(BaseModel):
    """Durable local artifact state with no provider URL."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(min_length=1)
    local_path: Path
    byte_size: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_type: str | None = None


class GenerationTaskRecord(BaseModel):
    """Provider-neutral persisted lifecycle state for one generation task."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    provider_task_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    external_correlation_id: str | None = None
    normalized_status: GenerationTaskStatus
    created_at: datetime
    updated_at: datetime
    artifact: ArtifactRecord | None = None

    @field_validator("created_at", "updated_at")
    @classmethod
    def timestamps_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Timestamps must be timezone-aware.")
        return value


class TaskRegistryError(RuntimeError):
    """Base safe error for local task-registry persistence failures."""


class TaskRegistryAlreadyExistsError(TaskRegistryError):
    """Raised when create would overwrite an existing task manifest."""


class TaskRegistryNotFoundError(TaskRegistryError):
    """Raised when a task manifest is absent."""


class TaskRegistryCorruptedManifestError(TaskRegistryError):
    """Raised when a manifest cannot be parsed as the durable record contract."""


class TaskRegistry:
    """Atomic filesystem persistence for provider-neutral generation task records."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or Path.cwd() / ".runtime" / "kling" / "tasks"

    def create(self, record: GenerationTaskRecord) -> None:
        destination = self._manifest_path(record.provider_task_id)
        if destination.exists():
            raise TaskRegistryAlreadyExistsError("A manifest already exists for this provider task ID.")
        self._write(record, destination, overwrite=False)

    def load(self, provider_task_id: str) -> GenerationTaskRecord:
        destination = self._manifest_path(provider_task_id)
        if not destination.is_file():
            raise TaskRegistryNotFoundError("No manifest exists for this provider task ID.")
        try:
            payload = json.loads(destination.read_text(encoding="utf-8"))
            return GenerationTaskRecord.model_validate(payload)
        except (OSError, json.JSONDecodeError, ValidationError) as error:
            raise TaskRegistryCorruptedManifestError(
                "The task manifest does not match the durable registry contract."
            ) from error

    def update(self, record: GenerationTaskRecord) -> None:
        destination = self._manifest_path(record.provider_task_id)
        if not destination.is_file():
            raise TaskRegistryNotFoundError("No manifest exists for this provider task ID.")
        self._write(record, destination, overwrite=True)

    def exists(self, provider_task_id: str) -> bool:
        return self._manifest_path(provider_task_id).is_file()

    def list(self) -> list[GenerationTaskRecord]:
        if not self._root.is_dir():
            return []
        return [self.load(path.stem) for path in sorted(self._root.glob("*.json"))]

    def _write(self, record: GenerationTaskRecord, destination: Path, overwrite: bool) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".json.part")
        try:
            with temporary.open("w", encoding="utf-8") as file:
                json.dump(record.model_dump(mode="json"), file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            if destination.exists() and not overwrite:
                raise TaskRegistryAlreadyExistsError("A manifest already exists for this provider task ID.")
            os.replace(temporary, destination)
        except TaskRegistryError:
            raise
        except OSError as error:
            raise TaskRegistryError("Task manifest could not be written atomically.") from error
        finally:
            temporary.unlink(missing_ok=True)

    def _manifest_path(self, provider_task_id: str) -> Path:
        try:
            validated = GenerationTaskRecord.model_validate(
                {
                    "provider": "registry",
                    "provider_task_id": provider_task_id,
                    "normalized_status": GenerationTaskStatus.SUBMITTED,
                    "created_at": datetime.now().astimezone(),
                    "updated_at": datetime.now().astimezone(),
                }
            ).provider_task_id
        except ValidationError as error:
            raise TaskRegistryError("Provider task ID is invalid for local registry storage.") from error
        return self._root / f"{validated}.json"
