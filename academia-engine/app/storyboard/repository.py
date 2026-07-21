import os
from pathlib import Path

from .contracts import CreativeStoryboard


class StoryboardPersistenceError(RuntimeError): pass
class StoryboardAlreadyExistsError(StoryboardPersistenceError): pass


class StoryboardRepository:
    def __init__(self, root: Path | None = None):
        self._root = Path(root or Path.cwd() / ".runtime" / "storyboards")

    def save(self, storyboard: CreativeStoryboard, *, overwrite=False) -> Path:
        destination = self._root / storyboard.storyboard_id / "storyboard.json"
        if destination.exists() and not overwrite:
            raise StoryboardAlreadyExistsError("Storyboard already exists.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".json.part")
        try:
            with temporary.open("x", encoding="utf-8", newline="") as stream:
                stream.write(storyboard.model_dump_json(indent=2)); stream.flush(); os.fsync(stream.fileno())
            if destination.exists() and not overwrite:
                raise StoryboardAlreadyExistsError("Storyboard already exists.")
            os.replace(temporary, destination)
            return destination
        except StoryboardPersistenceError:
            raise
        except OSError as error:
            raise StoryboardPersistenceError("Storyboard could not be persisted atomically.") from error
        finally:
            temporary.unlink(missing_ok=True)

    def load(self, storyboard_id: str) -> CreativeStoryboard:
        try:
            return CreativeStoryboard.model_validate_json((self._root / storyboard_id / "storyboard.json").read_text(encoding="utf-8"))
        except Exception as error:
            raise StoryboardPersistenceError("Storyboard could not be loaded safely.") from error
