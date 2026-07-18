import json
import os
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from .contracts import ProductionRecord


class ProductionRegistryError(RuntimeError): pass
class ProductionRegistryConflictError(ProductionRegistryError): pass
class ProductionRegistryNotFoundError(ProductionRegistryError): pass


class ProductionRegistry:
    def __init__(self, root: Path | None = None) -> None:
        self._root = root or Path.cwd() / ".runtime" / "productions"

    def create(self, record: ProductionRecord) -> None:
        path = self._path(record.production_id)
        if path.exists():
            raise ProductionRegistryConflictError("Production already exists.")
        self._write(record, path, False)

    def load(self, production_id: str) -> ProductionRecord:
        path = self._path(production_id)
        if not path.is_file():
            raise ProductionRegistryNotFoundError("Production was not found.")
        try:
            return ProductionRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as error:
            raise ProductionRegistryError("Production manifest is invalid.") from error

    def update(self, record: ProductionRecord) -> None:
        path = self._path(record.production_id)
        if not path.is_file():
            raise ProductionRegistryNotFoundError("Production was not found.")
        existing = self.load(record.production_id)
        old = tuple(scene.generation_request_reference for scene in existing.scenes)
        new = tuple(scene.generation_request_reference for scene in record.scenes)
        if old != new:
            raise ProductionRegistryError("Generation request references are immutable.")
        self._write(record, path, True)

    def exists(self, production_id: str) -> bool:
        return self._path(production_id).is_file()

    def _write(self, record: ProductionRecord, path: Path, overwrite: bool) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(".json.part")
        try:
            with part.open("w", encoding="utf-8") as stream:
                json.dump(record.model_dump(mode="json"), stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            if path.exists() and not overwrite:
                raise ProductionRegistryConflictError("Production already exists.")
            os.replace(part, path)
        except ProductionRegistryError:
            raise
        except OSError as error:
            raise ProductionRegistryError("Production manifest could not be written atomically.") from error
        finally:
            part.unlink(missing_ok=True)

    def _path(self, production_id: str) -> Path:
        if not production_id or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for character in production_id) or not production_id[0].isalnum():
            raise ProductionRegistryError("Production ID is invalid for registry storage.")
        return self._root / f"{production_id}.json"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
