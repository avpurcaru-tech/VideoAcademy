"""Read-only preflight and explicit final composition rendering (Sprint 18.8)."""
import hashlib,json,os
from enum import Enum
from pathlib import Path
from typing import Any,Callable,Protocol

from pydantic import BaseModel,ConfigDict,Field

from app.scene_planning import semantic_sha256
from .assets import AssetJobStatus,AssetReviewService
from .music import MusicStageService
from .planning_review import PlanningReviewService
from .workflow import WorkflowActionService,WorkflowStageStatus,WorkflowStateRepository

class CompositionUiError(RuntimeError): pass
class CompositionBlockedError(CompositionUiError): pass
class RenderConfirmationRequired(CompositionUiError): pass
class CompositionRenderStatus(str,Enum): GENERATED="generated"; APPROVED="approved"; REJECTED="rejected"; FAILED="failed"
class CompositionCheck(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    name:str; passed:bool; detail:str
class CompositionEdlEntry(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    scene_id:str; order:int=Field(ge=0); start_seconds:float=Field(ge=0); end_seconds:float=Field(gt=0); source_path:str
class CompositionRequest(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    project_id:str; music_version:int=Field(ge=1); music_variant_id:str; music_path:str
    approved_assets:dict[str,int]; edl:tuple[CompositionEdlEntry,...]; expected_duration_seconds:float=Field(gt=0)
    dependency_sha256:str=Field(pattern=r"^[a-f0-9]{64}$")
class CompositionPreflight(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    ready:bool; checks:tuple[CompositionCheck,...]; request:CompositionRequest|None=None
    asset_summary:dict[str,Any]=Field(default_factory=dict); music_summary:dict[str,Any]=Field(default_factory=dict)
class CompositionRenderResult(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    byte_size:int=Field(gt=0); sha256:str=Field(pattern=r"^[a-f0-9]{64}$"); duration_seconds:float=Field(gt=0)
class FinalCompositionRenderer(Protocol):
    def render(self,request:CompositionRequest,destination:Path)->CompositionRenderResult: ...
class ExistingFFmpegCompositionAdapter:
    """Delegates rendering to the existing composer through an injected request mapper."""
    def __init__(self,composer,request_mapper:Callable[[CompositionRequest,Path],Any]): self.composer=composer; self.request_mapper=request_mapper
    def render(self,request,destination):
        result=self.composer.compose(self.request_mapper(request,destination))
        return CompositionRenderResult(byte_size=result.byte_size,sha256=result.sha256,duration_seconds=request.expected_duration_seconds)
class CompositionVersionMetadata(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    version:int=Field(ge=1); status:CompositionRenderStatus; byte_size:int=Field(gt=0)
    sha256:str=Field(pattern=r"^[a-f0-9]{64}$"); duration_seconds:float=Field(gt=0); dependency_sha256:str=Field(pattern=r"^[a-f0-9]{64}$")

class CompositionReviewService:
    def __init__(self,project_directory,renderer=None): self.project=Path(project_directory); self.renderer=renderer; self.directory=self.project/"composition"
    def versions(self): return tuple(CompositionVersionMetadata.model_validate_json(x.read_text(encoding="utf-8")) for x in sorted(self.directory.glob("version-*/render-metadata.json")))
    def last_preflight(self):
        path=self.directory/"preflight.json"
        return CompositionPreflight.model_validate_json(path.read_text(encoding="utf-8")) if path.is_file() else None
    def preflight(self):
        workflow,_=WorkflowStateRepository(self.project).resolve(self.project.name); checks=[]
        for stage in ("music","alignment","scene_plan","visual_plan","prompts","assets"):
            value=workflow.stage(stage); checks.append(CompositionCheck(name=f"{stage}_approved",passed=value.status==WorkflowStageStatus.APPROVED,detail=value.status.value))
        request=None; asset_summary={}; music_summary={}
        try:
            music_stage=workflow.stage("music"); music_version=next(x for x in MusicStageService(self.project).versions() if x.version==music_stage.approved_version)
            if not music_version.approved_variant_id: raise ValueError("Approved music variant is missing.")
            variant=next(x for x in music_version.variants if x.variant_id==music_version.approved_variant_id)
            music_path=self.project/"music"/f"version-{music_version.version:03d}"/f"{variant.variant_id}.mp3"
            checks.append(CompositionCheck(name="music_exists",passed=music_path.is_file(),detail=str(music_path.relative_to(self.project))))
            music_summary={"version":music_version.version,"variant_id":variant.variant_id,"audio_id":variant.audio_id,"duration_seconds":variant.duration_seconds}
            prompts=PlanningReviewService(self.project).effective_prompts(); assets=AssetReviewService(self.project); asset_state=assets.state()
            approved={}; entries=[]; scene_plan=PlanningReviewService(self.project).selected("scene_plan")
            scene_data={x.get("scene_id"):x for x in (scene_plan.data.get("scenes",()) if scene_plan else ())}
            for order,prompt in enumerate(prompts):
                scene=asset_state.scene(prompt.scene_id)
                if scene.status!=AssetJobStatus.APPROVED or scene.approved_version is None: raise ValueError(f"Approved asset missing for {prompt.scene_id}.")
                path,metadata=assets.preview_path(prompt.scene_id,scene.approved_version); source=scene_data.get(prompt.scene_id,{})
                start=float(source.get("start",order)); end=float(source.get("end",start+(metadata.duration_seconds or 1)))
                if end<=start: raise ValueError(f"Invalid duration for {prompt.scene_id}.")
                approved[prompt.scene_id]=scene.approved_version; entries.append(CompositionEdlEntry(scene_id=prompt.scene_id,order=order,start_seconds=start,end_seconds=end,source_path=str(path.relative_to(self.project)).replace("\\","/")))
            checks.append(CompositionCheck(name="all_required_assets_approved",passed=True,detail=f"{len(approved)} assets"))
            checks.append(CompositionCheck(name="edl_valid",passed=bool(entries),detail=f"{len(entries)} entries"))
            missing=[x.source_path for x in entries if not (self.project/x.source_path).is_file()]
            checks.append(CompositionCheck(name="no_missing_source_files",passed=not missing,detail="none" if not missing else ", ".join(missing)))
            output_parent=self.directory; writable=output_parent.exists() and os.access(output_parent,os.W_OK) or (not output_parent.exists() and os.access(output_parent.parent,os.W_OK))
            checks.append(CompositionCheck(name="output_path_writable",passed=writable,detail="composition/"))
            duration=max(x.end_seconds for x in entries); core={"project_id":self.project.name,"music_version":music_version.version,
                "music_variant_id":variant.variant_id,"music_path":str(music_path.relative_to(self.project)).replace("\\","/"),
                "approved_assets":approved,"edl":[x.model_dump(mode="json") for x in entries],"expected_duration_seconds":duration}
            request=CompositionRequest(**core,dependency_sha256=semantic_sha256({"core":core,"workflow":{x:workflow.stage(x).approved_version for x in ("music","alignment","scene_plan","visual_plan","prompts","assets")}}))
            asset_summary={"required":len(entries),"approved":len(approved),"scene_ids":tuple(approved)}
        except (StopIteration,ValueError,OSError) as error: checks.append(CompositionCheck(name="dependency_resolution",passed=False,detail=str(error)))
        ready=request is not None and all(x.passed for x in checks)
        result=CompositionPreflight(ready=ready,checks=tuple(checks),request=request if ready else None,asset_summary=asset_summary,music_summary=music_summary)
        self._write(self.directory/"preflight.json",result,replace=True); return result
    def render(self,*,confirmed=False):
        if not confirmed: raise RenderConfirmationRequired("Explicit FFmpeg render confirmation is required.")
        preflight=self.preflight()
        if not preflight.ready or preflight.request is None: raise CompositionBlockedError("Composition preflight failed.")
        if self.renderer is None: raise CompositionUiError("Composition renderer is not configured.")
        number=len(self.versions())+1; directory=self.directory/f"version-{number:03d}"; directory.mkdir(parents=True,exist_ok=False)
        self._write(directory/"request.json",preflight.request); self._write(directory/"preflight.json",preflight)
        self._write(directory/"edl.json",{"entries":[x.model_dump(mode="json") for x in preflight.request.edl]})
        destination=directory/"final.mp4"; result=CompositionRenderResult.model_validate(self.renderer.render(preflight.request,destination))
        if not destination.is_file() or destination.stat().st_size<=0: raise CompositionUiError("Renderer did not create final.mp4.")
        actual=hashlib.sha256(destination.read_bytes()).hexdigest()
        if actual!=result.sha256 or destination.stat().st_size!=result.byte_size: raise CompositionUiError("Rendered output metadata is inconsistent.")
        metadata=CompositionVersionMetadata(version=number,status=CompositionRenderStatus.GENERATED,byte_size=result.byte_size,
            sha256=result.sha256,duration_seconds=result.duration_seconds,dependency_sha256=preflight.request.dependency_sha256)
        self._write(directory/"render-metadata.json",metadata); WorkflowActionService(self.project).execute(self.project.name,"mark_generated","composition",
            reason="explicit final render",artifact_path=f"composition/version-{number:03d}/final.mp4",artifact_sha256=result.sha256); return metadata
    def approve(self,version):
        value=self._get(version); updated=value.model_copy(update={"status":CompositionRenderStatus.APPROVED}); self._replace(updated)
        WorkflowActionService(self.project).execute(self.project.name,"approve","composition",reason="final approved"); return updated
    def reject(self,version):
        value=self._get(version); updated=value.model_copy(update={"status":CompositionRenderStatus.REJECTED}); self._replace(updated)
        WorkflowActionService(self.project).execute(self.project.name,"reject","composition",reason="final rejected"); return updated
    def preview_path(self,version):
        self._get(version); return self.directory/f"version-{int(version):03d}"/"final.mp4"
    def _get(self,version):
        path=self.directory/f"version-{int(version):03d}"/"render-metadata.json"
        if not path.is_file(): raise ValueError("Composition version does not exist.")
        return CompositionVersionMetadata.model_validate_json(path.read_text(encoding="utf-8"))
    def _replace(self,value): self._write(self.directory/f"version-{value.version:03d}"/"render-metadata.json",value,replace=True)
    @staticmethod
    def _write(path,value,replace=False):
        path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists() and not replace: raise FileExistsError("Composition artifact already exists.")
        payload=value.model_dump(mode="json") if isinstance(value,BaseModel) else value; part=path.with_suffix(path.suffix+".part")
        try: part.write_text(json.dumps(payload,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8"); os.replace(part,path)
        finally: part.unlink(missing_ok=True)
