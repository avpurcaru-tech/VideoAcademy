import os
from pathlib import Path

from .contracts import LyricsPlan


class LyricsPersistenceError(RuntimeError): pass
class LyricsOutputConflictError(LyricsPersistenceError): pass


def persist_lyrics_atomic(lyrics: LyricsPlan, output: Path, *, overwrite: bool=False) -> Path:
    destination=Path(output)
    if destination.exists() and not overwrite:
        raise LyricsOutputConflictError("Lyrics output already exists.")
    temporary=destination.with_suffix(destination.suffix+".part")
    destination.parent.mkdir(parents=True,exist_ok=True)
    try:
        with temporary.open("x",encoding="utf-8",newline="") as stream:
            stream.write(lyrics.to_json()); stream.flush(); os.fsync(stream.fileno())
        if destination.exists() and not overwrite:
            raise LyricsOutputConflictError("Lyrics output already exists.")
        os.replace(temporary,destination)
    except LyricsPersistenceError:
        raise
    except OSError as error:
        raise LyricsPersistenceError("Lyrics output could not be persisted atomically.") from error
    finally:
        temporary.unlink(missing_ok=True)
    return destination

