"""Versioned lyrics stage and injectable generation provider (Sprint 18.4)."""
import json,os
from enum import Enum
from pathlib import Path
from typing import Any,Protocol

from pydantic import BaseModel,ConfigDict,Field

from app.scene_planning import semantic_sha256
from .project_creation import WebProjectManifest
from .workflow import WorkflowActionService,WorkflowStateRepository

class LyricsVersionStatus(str,Enum): GENERATED="generated"; FAILED="failed"
class LyricsGenerationRequest(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    episode_title:str; description:str; theme:str|None=None; educational_goal:str|None=None
    language:str; target_age:str; main_character_name:str; main_character_description:str
    user_instructions:str|None=None; feedback:str|None=None
class LyricsGenerationResult(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    lyrics_text:str=Field(min_length=1,max_length=50000); sections:tuple[str,...]=(); provider_metadata:dict[str,Any]=Field(default_factory=dict)
class LyricsGenerationProvider(Protocol):
    def generate(self,request:LyricsGenerationRequest)->LyricsGenerationResult: ...
class LyricsVersion(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    version:int=Field(ge=1); lyrics_text:str; sections:tuple[str,...]=(); provider_metadata:dict[str,Any]=Field(default_factory=dict)
    generation_request_sha256:str=Field(pattern=r"^[a-f0-9]{64}$"); generation_request:LyricsGenerationRequest|None=None
    status:LyricsVersionStatus; error:str|None=None
class LyricsGenerationFailure(RuntimeError):
    def __init__(self,message,version): super().__init__(message); self.version=version

class LyricsStageService:
    def __init__(self,project_directory,provider=None): self.project=Path(project_directory); self.provider=provider
    def versions(self):
        result=[]
        for path in sorted((self.project/"lyrics").glob("version-*.json")):
            result.append(LyricsVersion.model_validate_json(path.read_text(encoding="utf-8")))
        return tuple(result)
    def selected(self):
        versions={x.version:x for x in self.versions()}; state,_=WorkflowStateRepository(self.project).resolve(self.project.name)
        selected=state.stage("lyrics").selected_version
        return versions.get(selected) if selected else (versions[max(versions)] if versions else None)
    def generate(self,*,feedback=None,user_instructions=None):
        request=self._request(feedback=feedback,user_instructions=user_instructions)
        if self.provider is None: return self._failure(request,"Lyrics generation provider is not configured.")
        try: result=LyricsGenerationResult.model_validate(self.provider.generate(request))
        except Exception as error: return self._failure(request,str(error) or "Lyrics generation failed.")
        return self._persist(request,result.lyrics_text,result.sections,result.provider_metadata,LyricsVersionStatus.GENERATED)
    def edit(self,text):
        text=str(text).strip()
        if not text: raise ValueError("Lyrics text is required.")
        request=self._request(user_instructions="manual_edit")
        return self._persist(request,text,self._sections(text),{"source":"manual_edit"},LyricsVersionStatus.GENERATED)
    def _failure(self,request,message):
        version=self._persist(request,"",(),{"source":"provider"},LyricsVersionStatus.FAILED,error=message)
        WorkflowActionService(self.project).execute(self.project.name,"mark_failed","lyrics",reason=message)
        raise LyricsGenerationFailure("Lyrics generation failed.",version)
    def _persist(self,request,text,sections,metadata,status,error=None):
        state,_=WorkflowStateRepository(self.project).resolve(self.project.name); number=state.stage("lyrics").current_version+1
        version=LyricsVersion(version=number,lyrics_text=text,sections=tuple(sections),provider_metadata=dict(metadata),
            generation_request_sha256=semantic_sha256(request),generation_request=request,status=status,error=error)
        path=self.project/"lyrics"/f"version-{number:03d}.json"; self._write(path,version)
        WorkflowActionService(self.project).execute(self.project.name,"mark_generated","lyrics",reason=("manual edit" if metadata.get("source")=="manual_edit" else "lyrics generation"),
            artifact_path=f"lyrics/{path.name}",artifact_sha256=semantic_sha256(version))
        return version
    def _request(self,feedback=None,user_instructions=None):
        manifest=WebProjectManifest.model_validate_json((self.project/"project.json").read_text(encoding="utf-8"))
        episode=manifest.episode; character=manifest.main_character
        return LyricsGenerationRequest(episode_title=episode.title,description=episode.description,theme=episode.episode_theme,
            educational_goal=episode.educational_goal,language=episode.language,target_age=episode.target_age,
            main_character_name=character.name,main_character_description=character.description,
            user_instructions=(user_instructions.strip() or None) if user_instructions else None,
            feedback=(feedback.strip() or None) if feedback else None)
    @staticmethod
    def _sections(text): return tuple(line.strip() for line in text.splitlines() if line.strip().startswith("[") and line.strip().endswith("]"))
    @staticmethod
    def _write(path,value):
        path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists(): raise FileExistsError("Lyrics version already exists.")
        part=path.with_suffix(path.suffix+".part")
        try:
            part.write_text(json.dumps(value.model_dump(mode="json"),ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8")
            with part.open("r+b") as stream: os.fsync(stream.fileno())
            os.replace(part,path)
        finally: part.unlink(missing_ok=True)
