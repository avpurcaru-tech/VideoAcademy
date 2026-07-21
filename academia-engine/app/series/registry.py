import os
from pathlib import Path

from pydantic import ValidationError

from .contracts import SeriesBible


class SeriesRegistryError(RuntimeError): pass
class SeriesNotFoundError(SeriesRegistryError): pass
class CorruptedSeriesBibleError(SeriesRegistryError): pass
class ConflictingSeriesBibleError(SeriesRegistryError): pass


class SeriesRegistry:
    def __init__(self, root: Path | None = None, character_registry=None):
        self._root = Path(root or Path.cwd() / ".runtime" / "series")
        self._characters = character_registry

    def path_for(self, series_id: str) -> Path:
        return self._root / series_id / "series-bible.json"

    def register(self, bible: SeriesBible) -> Path:
        bible = SeriesBible.model_validate(bible)
        if self._characters is not None: self._characters.require_many(bible.resolved_character_ids)
        destination = self.path_for(bible.series_id)
        serialized = bible.model_dump_json(indent=2)
        if destination.exists():
            try: existing = SeriesBible.model_validate_json(destination.read_text(encoding="utf-8"))
            except (OSError, ValidationError) as error: raise CorruptedSeriesBibleError("Existing Series Bible is corrupted.") from error
            if existing == bible: return destination
            raise ConflictingSeriesBibleError("A different Series Bible is already registered for this series ID.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".json.part")
        try:
            with temporary.open("x", encoding="utf-8", newline="") as stream:
                stream.write(serialized); stream.flush(); os.fsync(stream.fileno())
            if destination.exists(): raise ConflictingSeriesBibleError("Series Bible registration conflicted with another writer.")
            os.replace(temporary, destination)
            return destination
        except SeriesRegistryError: raise
        except OSError as error: raise SeriesRegistryError("Series Bible could not be registered atomically.") from error
        finally: temporary.unlink(missing_ok=True)

    def load(self, series_id: str) -> SeriesBible:
        destination = self.path_for(series_id)
        if not destination.is_file(): raise SeriesNotFoundError("Referenced series is not registered.")
        try:
            bible=SeriesBible.model_validate_json(destination.read_text(encoding="utf-8"))
            if self._characters is not None: self._characters.require_many(bible.resolved_character_ids)
            return bible
        except (OSError, ValidationError) as error: raise CorruptedSeriesBibleError("Registered Series Bible is corrupted.") from error
