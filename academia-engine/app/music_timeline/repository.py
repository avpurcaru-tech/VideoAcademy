import os
from pathlib import Path

from .contracts import MusicTimeline


class MusicTimelinePersistenceError(RuntimeError): pass
class MusicTimelineAlreadyExistsError(MusicTimelinePersistenceError): pass


class MusicTimelineRepository:
    def __init__(self, root: Path | None = None):
        self._root = Path(root or Path.cwd() / ".runtime" / "music-timelines")

    def save(self, timeline: MusicTimeline, *, overwrite=False) -> Path:
        destination = self._root / timeline.storyboard_id / "timeline.json"
        if destination.exists() and not overwrite:
            raise MusicTimelineAlreadyExistsError("Music timeline already exists.")
        destination.parent.mkdir(parents=True, exist_ok=True); temporary = destination.with_suffix(".json.part")
        try:
            with temporary.open("x", encoding="utf-8", newline="") as stream:
                stream.write(timeline.model_dump_json(indent=2)); stream.flush(); os.fsync(stream.fileno())
            if destination.exists() and not overwrite:
                raise MusicTimelineAlreadyExistsError("Music timeline already exists.")
            os.replace(temporary, destination); return destination
        except MusicTimelinePersistenceError: raise
        except OSError as error: raise MusicTimelinePersistenceError("Music timeline could not be persisted atomically.") from error
        finally: temporary.unlink(missing_ok=True)

    def load(self, storyboard_id: str) -> MusicTimeline:
        try:
            return MusicTimeline.model_validate_json((self._root / storyboard_id / "timeline.json").read_text(encoding="utf-8"))
        except Exception as error: raise MusicTimelinePersistenceError("Music timeline could not be loaded safely.") from error
