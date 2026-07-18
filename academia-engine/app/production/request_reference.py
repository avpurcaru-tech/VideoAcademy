import json
import os
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.models import VideoGenerationRequest


class GenerationRequestReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    reference_id: str = Field(min_length=1, max_length=200, pattern=r"^[a-z0-9][a-z0-9_-]*$")

    def __str__(self) -> str:
        return self.reference_id


class GenerationRequestResolverError(RuntimeError): pass
class GenerationRequestNotFoundError(GenerationRequestResolverError): pass
class GenerationRequestCorruptedError(GenerationRequestResolverError): pass
class GenerationRequestConflictError(GenerationRequestResolverError): pass


@runtime_checkable
class GenerationRequestResolver(Protocol):
    def resolve(self, reference: GenerationRequestReference) -> VideoGenerationRequest: ...


class GenerationRequestStore:
    """Atomic store for provider-neutral semantic generation requests, never provider HTTP payloads."""
    def __init__(self, root: Path | None = None) -> None:
        self._root = root or Path.cwd() / ".runtime" / "requests"

    def create(self, reference: GenerationRequestReference, request: VideoGenerationRequest) -> None:
        path = self._path(reference)
        if path.is_file():
            existing = self.resolve(reference)
            if existing != request:
                raise GenerationRequestConflictError("Generation request reference already has different content.")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(".json.part")
        try:
            with part.open("w", encoding="utf-8") as stream:
                json.dump(request.model_dump(mode="json"), stream, ensure_ascii=False, separators=(",", ":"))
                stream.flush(); os.fsync(stream.fileno())
            if path.exists():
                raise GenerationRequestConflictError("Generation request reference already exists.")
            os.replace(part, path)
        except GenerationRequestResolverError:
            raise
        except OSError as error:
            raise GenerationRequestResolverError("Generation request could not be stored atomically.") from error
        finally:
            part.unlink(missing_ok=True)

    def resolve(self, reference: GenerationRequestReference) -> VideoGenerationRequest:
        path = self._path(reference)
        if not path.is_file():
            raise GenerationRequestNotFoundError("Generation request reference was not found.")
        try:
            return VideoGenerationRequest.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as error:
            raise GenerationRequestCorruptedError("Generation request record is corrupted.") from error

    def _path(self, reference: GenerationRequestReference) -> Path:
        try:
            validated = GenerationRequestReference.model_validate(reference)
        except ValidationError as error:
            raise GenerationRequestResolverError("Generation request reference is invalid.") from error
        return self._root / f"{validated.reference_id}.json"
