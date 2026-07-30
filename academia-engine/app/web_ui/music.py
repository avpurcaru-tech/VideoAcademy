"""Explicit, versioned music generation UI service (Sprint 18.5)."""
import hashlib,json,os,time
from enum import Enum
from pathlib import Path
from typing import Any,Callable,Protocol

from pydantic import BaseModel,ConfigDict,Field

from app.scene_planning import semantic_sha256
from app.models import GenerationTaskStatus
from .lyrics import LyricsVersion
from .project_creation import WebProjectManifest
from .workflow import WorkflowActionService,WorkflowStageStatus,WorkflowStateRepository

DEFAULT_MUSICAL_STYLE="Romanian children’s song, preschool educational, slow nursery rhyme, bright cartoon chorus, simple major key, plate reverb, light compression, wide stereo mix"
DEFAULT_MUSIC_MOOD="playful"
DEFAULT_MUSIC_INSTRUMENTATION=("clapping percussion","toy piano","glockenspiel melody","ukulele strumming","hand drum taps","bass xylophone","finger snaps")
DEFAULT_MUSIC_VOCAL_STYLE="playful female vocals, call and response"
DEFAULT_MUSIC_TEMPO_BPM=92

class MusicUiError(RuntimeError): pass
class MusicBlockedError(MusicUiError): pass
class MusicCostConfirmationRequired(MusicUiError): pass
class MusicVersionStatus(str,Enum): GENERATED="generated"; APPROVED="approved"; REJECTED="rejected"; FAILED="failed"
class MusicGenerationRequest(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    project_id:str; episode_title:str; language:str; target_age:str; lyrics_version:int=Field(ge=1)
    lyrics_text:str=Field(min_length=1); lyrics_sha256:str=Field(pattern=r"^[a-f0-9]{64}$"); regeneration_feedback:str|None=None
    musical_style:str=Field(default=DEFAULT_MUSICAL_STYLE,min_length=1,max_length=500)
    mood:str=Field(default=DEFAULT_MUSIC_MOOD,min_length=1,max_length=200)
    instrumentation:tuple[str,...]=Field(default=DEFAULT_MUSIC_INSTRUMENTATION,min_length=1)
    vocal_style:str=Field(default=DEFAULT_MUSIC_VOCAL_STYLE,min_length=1,max_length=300)
    tempo_bpm:float=Field(default=DEFAULT_MUSIC_TEMPO_BPM,gt=0,le=300)
class MusicVariantResult(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    audio_id:str=Field(min_length=1,max_length=200); audio_bytes:bytes; duration_seconds:float|None=Field(default=None,gt=0)
    content_type:str="audio/mpeg"; metadata:dict[str,Any]=Field(default_factory=dict)
class MusicGenerationResult(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    task_id:str=Field(min_length=1,max_length=200); variants:tuple[MusicVariantResult,...]=Field(min_length=1); provider_metadata:dict[str,Any]=Field(default_factory=dict)
class MusicGenerationProvider(Protocol):
    def generate(self,request:MusicGenerationRequest)->MusicGenerationResult: ...

class SunoApiOrgMusicAdapter:
    """Adapter over the existing submit/query/download provider contract."""
    def __init__(self,provider,request_mapper:Callable[[MusicGenerationRequest],Any],*,poll_interval_seconds=5,generation_timeout_seconds=900,clock=time):
        self.provider=provider; self.request_mapper=request_mapper; self.poll_interval_seconds=float(poll_interval_seconds); self.generation_timeout_seconds=float(generation_timeout_seconds); self.clock=clock
        if self.poll_interval_seconds<=0 or self.generation_timeout_seconds<=0: raise ValueError("Suno polling intervals must be positive.")
    def generate(self,request):
        submitted=self.provider.submit_generation(self.request_mapper(request)); deadline=self.clock.monotonic()+self.generation_timeout_seconds
        while True:
            task=self.provider.get_task_by_id(submitted.provider_task_id)
            if task.normalized_status==GenerationTaskStatus.FAILED: raise MusicUiError("Generarea muzicii a eșuat la providerul Suno.")
            if task.normalized_status==GenerationTaskStatus.SUCCEEDED:
                if not task.artifacts: raise MusicUiError("Suno a finalizat jobul fără variante audio.")
                break
            remaining=deadline-self.clock.monotonic()
            if remaining<=0: raise MusicUiError("Generarea muzicii Suno a depășit timpul maxim de 15 minute.")
            self.clock.sleep(min(self.poll_interval_seconds,remaining))
        variants=tuple(MusicVariantResult(audio_id=x.artifact_id,audio_bytes=self.provider.download_audio_bytes(x),
            duration_seconds=x.duration_seconds,content_type=x.content_type) for x in task.artifacts)
        return MusicGenerationResult(task_id=submitted.provider_task_id,variants=variants,provider_metadata={"provider":"sunoapi_org"})

class MusicVariantManifest(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True,populate_by_name=True)
    variant_id:str=Field(pattern=r"^variant-[0-9]{2}$"); audio_id:str=Field(alias="audioId"); duration_seconds:float|None=None
    content_type:str; byte_size:int=Field(gt=0); sha256:str=Field(pattern=r"^[a-f0-9]{64}$"); metadata:dict[str,Any]=Field(default_factory=dict)
class MusicVersionManifest(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True,populate_by_name=True)
    version:int=Field(ge=1); task_id:str=Field(alias="taskId"); lyrics_version:int=Field(ge=1); lyrics_sha256:str=Field(pattern=r"^[a-f0-9]{64}$")
    request_sha256:str=Field(pattern=r"^[a-f0-9]{64}$"); request:MusicGenerationRequest
    variants:tuple[MusicVariantManifest,...]; selected_variant_id:str|None=None; approved_variant_id:str|None=None
    status:MusicVersionStatus=MusicVersionStatus.GENERATED

class MusicStageService:
    def __init__(self,project_directory,provider=None): self.project=Path(project_directory); self.provider=provider
    def versions(self):
        return tuple(MusicVersionManifest.model_validate_json(path.read_text(encoding="utf-8"))
            for path in sorted((self.project/"music").glob("version-*/job.json")))
    def generate(self,*,confirmed=False,feedback=None,musical_style=DEFAULT_MUSICAL_STYLE,mood=DEFAULT_MUSIC_MOOD,
            instrumentation=", ".join(DEFAULT_MUSIC_INSTRUMENTATION),vocal_style=DEFAULT_MUSIC_VOCAL_STYLE,tempo_bpm=DEFAULT_MUSIC_TEMPO_BPM):
        if not confirmed: raise MusicCostConfirmationRequired("Explicit Suno cost confirmation is required.")
        state,_=WorkflowStateRepository(self.project).resolve(self.project.name); lyrics_stage=state.stage("lyrics")
        if lyrics_stage.status!=WorkflowStageStatus.APPROVED or lyrics_stage.approved_version is None: raise MusicBlockedError("Approved lyrics are required.")
        if self.provider is None: raise MusicUiError("Music generation provider is not configured.")
        lyrics=LyricsVersion.model_validate_json((self.project/"lyrics"/f"version-{lyrics_stage.approved_version:03d}.json").read_text(encoding="utf-8"))
        manifest=WebProjectManifest.model_validate_json((self.project/"project.json").read_text(encoding="utf-8"))
        request=MusicGenerationRequest(project_id=self.project.name,episode_title=manifest.episode.title,language=manifest.episode.language,
            target_age=manifest.episode.target_age,lyrics_version=lyrics.version,lyrics_text=lyrics.lyrics_text,
            lyrics_sha256=semantic_sha256(lyrics),regeneration_feedback=(feedback.strip() or None) if feedback else None,
            musical_style=str(musical_style).strip(),mood=str(mood).strip(),
            instrumentation=tuple(value.strip() for value in str(instrumentation).split(",") if value.strip()),
            vocal_style=str(vocal_style).strip(),tempo_bpm=float(tempo_bpm))
        result=MusicGenerationResult.model_validate(self.provider.generate(request)); number=state.stage("music").current_version+1
        directory=self.project/"music"/f"version-{number:03d}"; directory.mkdir(parents=True,exist_ok=False); variants=[]
        for index,value in enumerate(result.variants,1):
            variant_id=f"variant-{index:02d}"; audio_path=directory/f"{variant_id}.mp3"; self._write_bytes(audio_path,value.audio_bytes)
            item=MusicVariantManifest(variant_id=variant_id,audio_id=value.audio_id,duration_seconds=value.duration_seconds,
                content_type=value.content_type,byte_size=len(value.audio_bytes),sha256=hashlib.sha256(value.audio_bytes).hexdigest(),metadata=value.metadata)
            self._write_json(directory/f"{variant_id}.json",item.model_dump(mode="json",by_alias=True)); variants.append(item)
        version=MusicVersionManifest(version=number,task_id=result.task_id,lyrics_version=lyrics.version,lyrics_sha256=request.lyrics_sha256,
            request_sha256=semantic_sha256(request),request=request,variants=tuple(variants))
        self._write_json(directory/"job.json",version.model_dump(mode="json",by_alias=True))
        WorkflowActionService(self.project).execute(self.project.name,"mark_generated","music",reason="music generation",
            artifact_path=f"music/version-{number:03d}/job.json",artifact_sha256=semantic_sha256(version))
        return version
    def select(self,version,variant_id):
        current=self._get(version); self._variant(current,variant_id)
        updated=current.model_copy(update={"selected_variant_id":variant_id,"status":MusicVersionStatus.GENERATED})
        self._replace_job(updated); WorkflowActionService(self.project).execute(self.project.name,"select_version","music",version=version,reason=f"selected {variant_id}")
        return updated
    def approve(self,version,variant_id=None):
        current=self._get(version); selected=variant_id or current.selected_variant_id
        if not selected: raise ValueError("A music variant must be selected.")
        self._variant(current,selected); updated=current.model_copy(update={"selected_variant_id":selected,"approved_variant_id":selected,"status":MusicVersionStatus.APPROVED})
        self._replace_job(updated); WorkflowActionService(self.project).execute(self.project.name,"approve","music",reason=f"approved {selected}"); return updated
    def reject(self,version):
        current=self._get(version); updated=current.model_copy(update={"status":MusicVersionStatus.REJECTED}); self._replace_job(updated)
        WorkflowActionService(self.project).execute(self.project.name,"reject","music",reason="music rejected"); return updated
    def audio_path(self,version,variant_id):
        current=self._get(version); self._variant(current,variant_id); return self.project/"music"/f"version-{version:03d}"/f"{variant_id}.mp3"
    def _get(self,version):
        path=self.project/"music"/f"version-{int(version):03d}"/"job.json"
        if not path.is_file(): raise ValueError("Music version does not exist.")
        return MusicVersionManifest.model_validate_json(path.read_text(encoding="utf-8"))
    @staticmethod
    def _variant(version,variant_id): return next((x for x in version.variants if x.variant_id==variant_id),None) or (_ for _ in ()).throw(ValueError("Music variant does not exist."))
    def _replace_job(self,value):
        path=self.project/"music"/f"version-{value.version:03d}"/"job.json"; part=path.with_suffix(".json.part")
        try: part.write_text(json.dumps(value.model_dump(mode="json",by_alias=True),ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8"); os.replace(part,path)
        finally: part.unlink(missing_ok=True)
    @staticmethod
    def _write_json(path,payload):
        path.write_text(json.dumps(payload,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    @staticmethod
    def _write_bytes(path,payload):
        if not payload: raise ValueError("Audio variant is empty.")
        with path.open("xb") as stream: stream.write(payload); stream.flush(); os.fsync(stream.fileno())
