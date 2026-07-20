import json
import os
from pathlib import Path

from pydantic import ValidationError

from .contracts import MusicGenerationTaskRecord


class MusicTaskRegistryError(RuntimeError): pass
class MusicTaskRegistryConflictError(MusicTaskRegistryError): pass
class MusicTaskRegistryNotFoundError(MusicTaskRegistryError): pass
class MusicTaskRegistryCorruptedError(MusicTaskRegistryError): pass


class MusicTaskRegistry:
    def __init__(self,root: Path | None=None) -> None:
        self._root=root or Path.cwd()/".runtime"/"music"/"tasks"

    def create(self,record: MusicGenerationTaskRecord) -> None:
        path=self._path(record.provider_task_id)
        if path.exists(): raise MusicTaskRegistryConflictError("Music task already exists.")
        self._write(record,path,False)

    def load(self,provider_task_id: str) -> MusicGenerationTaskRecord:
        path=self._path(provider_task_id)
        if not path.is_file(): raise MusicTaskRegistryNotFoundError("Music task was not found.")
        try: return MusicGenerationTaskRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError,ValidationError) as error: raise MusicTaskRegistryCorruptedError("Music task record is corrupted.") from error

    def update(self,record: MusicGenerationTaskRecord) -> None:
        path=self._path(record.provider_task_id)
        if not path.is_file(): raise MusicTaskRegistryNotFoundError("Music task was not found.")
        self._write(record,path,True)

    def exists(self,provider_task_id: str) -> bool: return self._path(provider_task_id).is_file()

    def list(self) -> list[MusicGenerationTaskRecord]:
        if not self._root.is_dir(): return []
        return [self.load(path.stem) for path in sorted(self._root.glob("*.json"))]

    def _write(self,record,path,overwrite):
        path.parent.mkdir(parents=True,exist_ok=True); part=path.with_suffix(".json.part")
        try:
            with part.open("w",encoding="utf-8") as stream:
                json.dump(record.model_dump(mode="json"),stream,ensure_ascii=False,indent=2)
                stream.flush(); os.fsync(stream.fileno())
            if path.exists() and not overwrite: raise MusicTaskRegistryConflictError("Music task already exists.")
            os.replace(part,path)
        except MusicTaskRegistryError: raise
        except OSError as error: raise MusicTaskRegistryError("Music task could not be written atomically.") from error
        finally: part.unlink(missing_ok=True)

    def _path(self,task_id):
        if not task_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in task_id):
            raise MusicTaskRegistryError("Music provider task ID is invalid.")
        return self._root/f"{task_id}.json"

