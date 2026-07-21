import os
from pathlib import Path


class EpisodeWriteError(RuntimeError): pass
class EpisodeOutputConflictError(EpisodeWriteError): pass


def persist_episode_atomic(episode,destination: Path,overwrite=False):
    destination=Path(destination)
    if destination.exists() and not overwrite: raise EpisodeOutputConflictError("Episode output already exists.")
    destination.parent.mkdir(parents=True,exist_ok=True); part=destination.with_suffix(destination.suffix+".part")
    try:
        with part.open("x",encoding="utf-8") as stream:
            stream.write(episode.model_dump_json(indent=2)); stream.flush(); os.fsync(stream.fileno())
        if destination.exists() and not overwrite: raise EpisodeOutputConflictError("Episode output already exists.")
        os.replace(part,destination); return destination
    except EpisodeWriteError: raise
    except OSError as error: raise EpisodeWriteError("Episode output could not be persisted atomically.") from error
    finally: part.unlink(missing_ok=True)
