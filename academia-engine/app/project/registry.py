import json
import os
from pathlib import Path

from pydantic import ValidationError

from .contracts import ProjectRecord


class ProjectRegistryError(RuntimeError): pass
class ProjectConflictError(ProjectRegistryError): pass
class ProjectNotFoundError(ProjectRegistryError): pass
class ProjectCorruptedError(ProjectRegistryError): pass


class ProjectRegistry:
    def __init__(self,root: Path|None=None): self._root=Path(root or Path.cwd()/".runtime"/"projects")
    def exists(self,project_id): return self._path(project_id).is_file()
    def create(self,record):
        path=self._path(record.project_id)
        if path.exists(): raise ProjectConflictError("Project already exists.")
        self._write(record,path,False)
    def load(self,project_id):
        path=self._path(project_id)
        if not path.is_file(): raise ProjectNotFoundError("Project was not found.")
        try: return ProjectRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError,ValidationError) as error: raise ProjectCorruptedError("Project record is corrupted.") from error
    def update(self,record):
        path=self._path(record.project_id)
        if not path.is_file(): raise ProjectNotFoundError("Project was not found.")
        self._write(record,path,True)
    def _path(self,project_id):
        if not project_id or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in project_id):
            raise ProjectRegistryError("Project ID is invalid.")
        return self._root/project_id/"project.json"
    def _write(self,record,path,overwrite):
        path.parent.mkdir(parents=True,exist_ok=True); part=path.with_suffix(".json.part")
        try:
            with part.open("w",encoding="utf-8") as stream:
                json.dump(record.model_dump(mode="json"),stream,ensure_ascii=False,indent=2); stream.flush(); os.fsync(stream.fileno())
            if path.exists() and not overwrite: raise ProjectConflictError("Project already exists.")
            os.replace(part,path)
        except ProjectRegistryError: raise
        except OSError as error: raise ProjectRegistryError("Project could not be written atomically.") from error
        finally: part.unlink(missing_ok=True)
