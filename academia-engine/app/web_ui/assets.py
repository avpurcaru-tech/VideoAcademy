"""Per-scene visual asset generation and review (Sprint 18.7)."""
import hashlib,json,os,re
from enum import Enum
from pathlib import Path
from typing import Any,Protocol

from pydantic import BaseModel,ConfigDict,Field

from app.scene_planning import semantic_sha256
from .planning_review import AssetStalenessState,PlanningReviewService
from .workflow import ArtifactVersion,WorkflowActionService,WorkflowStageStatus,WorkflowStateMachine,WorkflowStateRepository

class AssetReviewError(RuntimeError): pass
class AssetBlockedError(AssetReviewError): pass
class AssetCostConfirmationRequired(AssetReviewError): pass
class AssetGenerationFailure(AssetReviewError): pass
class AssetJobStatus(str,Enum):
    NOT_STARTED="not_started"; SUBMITTED="submitted"; PROCESSING="processing"; COMPLETED="completed"
    FAILED="failed"; APPROVED="approved"; REJECTED="rejected"; STALE="stale"
class AssetMediaType(str,Enum): IMAGE="image"; VIDEO="video"
class AssetGenerationRequest(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    project_id:str; scene_id:str; prompt_bundle_version:int=Field(ge=1); positive_prompt:str
    negative_prompt:str=""; structured_parameters:dict[str,Any]=Field(default_factory=dict)
    prompt_sha256:str=Field(pattern=r"^[a-f0-9]{64}$"); feedback:str|None=None
class AssetGenerationJob(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    job_id:str; provider:str; status:AssetJobStatus; provider_metadata:dict[str,Any]=Field(default_factory=dict)
class AssetGenerationResult(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    job:AssetGenerationJob; media_type:AssetMediaType; content_type:str; content:bytes
    duration_seconds:float|None=Field(default=None,gt=0); provider_response:dict[str,Any]=Field(default_factory=dict)
class VisualAssetProvider(Protocol):
    def generate(self,request:AssetGenerationRequest)->AssetGenerationResult: ...
class VisualAssetPollingProvider(Protocol):
    def poll(self,job:AssetGenerationJob)->AssetGenerationResult: ...
class AssetVersionMetadata(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    scene_id:str; version:int=Field(ge=1); status:AssetJobStatus; provider:str; job_id:str
    media_type:AssetMediaType; content_type:str; duration_seconds:float|None=None; byte_size:int=Field(gt=0)
    sha256:str=Field(pattern=r"^[a-f0-9]{64}$"); prompt_sha256:str=Field(pattern=r"^[a-f0-9]{64}$"); filename:str
class AssetSceneState(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    scene_id:str; status:AssetJobStatus=AssetJobStatus.NOT_STARTED; current_version:int=0
    selected_version:int|None=None; approved_version:int|None=None; versions:tuple[int,...]=()
class AssetReviewState(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    scenes:tuple[AssetSceneState,...]=()
    def scene(self,scene_id): return next((x for x in self.scenes if x.scene_id==scene_id),AssetSceneState(scene_id=scene_id))

class AssetReviewService:
    def __init__(self,project_directory,provider=None): self.project=Path(project_directory); self.provider=provider; self.state_path=self.project/"assets"/"state.json"
    def state(self): return AssetReviewState.model_validate_json(self.state_path.read_text(encoding="utf-8")) if self.state_path.is_file() else AssetReviewState()
    def prompts(self): return PlanningReviewService(self.project).effective_prompts()
    def generate(self,scene_id,*,confirmed=False,feedback=None):
        self._scene_id(scene_id)
        if not confirmed: raise AssetCostConfirmationRequired("Explicit asset cost confirmation is required.")
        workflow,_=WorkflowStateRepository(self.project).resolve(self.project.name)
        if workflow.stage("prompts").status!=WorkflowStageStatus.APPROVED: raise AssetBlockedError("Approved prompts are required.")
        prompt=next((x for x in self.prompts() if x.scene_id==scene_id),None)
        if prompt is None: raise ValueError("Prompt scene does not exist.")
        if self.provider is None: raise AssetReviewError("Visual asset provider is not configured.")
        scene_state=self.state().scene(scene_id); number=scene_state.current_version+1
        bundle=PlanningReviewService(self.project).selected("prompts")
        request=AssetGenerationRequest(project_id=self.project.name,scene_id=scene_id,prompt_bundle_version=bundle.version,
            positive_prompt=prompt.positive_prompt,negative_prompt=prompt.negative_prompt,structured_parameters=prompt.structured_parameters,
            prompt_sha256=semantic_sha256(prompt),feedback=(feedback.strip() or None) if feedback else None)
        directory=self.project/"assets"/scene_id/f"version-{number:03d}"; directory.mkdir(parents=True,exist_ok=False)
        self._write_json(directory/"request.json",request.model_dump(mode="json"))
        try: result=AssetGenerationResult.model_validate(self.provider.generate(request))
        except Exception as error:
            self._write_json(directory/"provider-response.json",{"status":"failed","error":str(error)[:500]})
            self._update(scene_state.model_copy(update={"status":AssetJobStatus.FAILED,"current_version":number,
                "selected_version":number,"versions":scene_state.versions+(number,)})); raise AssetGenerationFailure("Asset generation failed.") from error
        if result.job.status!=AssetJobStatus.COMPLETED or not result.content: raise AssetGenerationFailure("Provider job is not completed.")
        extension=".png" if result.media_type==AssetMediaType.IMAGE else ".mp4"; filename="asset"+extension
        self._write_bytes(directory/filename,result.content); self._write_json(directory/"provider-response.json",{
            "job_id":result.job.job_id,"provider":result.job.provider,"status":result.job.status.value,
            "provider_metadata":result.job.provider_metadata,"response":result.provider_response})
        metadata=AssetVersionMetadata(scene_id=scene_id,version=number,status=AssetJobStatus.COMPLETED,provider=result.job.provider,
            job_id=result.job.job_id,media_type=result.media_type,content_type=result.content_type,duration_seconds=result.duration_seconds,
            byte_size=len(result.content),sha256=hashlib.sha256(result.content).hexdigest(),prompt_sha256=request.prompt_sha256,filename=filename)
        self._write_json(directory/"metadata.json",metadata.model_dump(mode="json")); self._update(scene_state.model_copy(update={
            "status":AssetJobStatus.COMPLETED,"current_version":number,"selected_version":number,"versions":scene_state.versions+(number,)}))
        self._set_global_generated(); self._clear_prompt_stale(scene_id); self._stale_composition(scene_id); return metadata
    def select(self,scene_id,version):
        scene=self.state().scene(scene_id); self.metadata(scene_id,version)
        updated=scene.model_copy(update={"selected_version":version,"status":AssetJobStatus.COMPLETED}); self._update(updated); self._stale_composition(scene_id); return updated
    def approve(self,scene_id):
        scene=self.state().scene(scene_id)
        if scene.selected_version is None: raise ValueError("An asset version must be selected.")
        self.metadata(scene_id,scene.selected_version); updated=scene.model_copy(update={"approved_version":scene.selected_version,"status":AssetJobStatus.APPROVED}); self._update(updated); self._approve_global_if_complete(); return updated
    def reject(self,scene_id):
        scene=self.state().scene(scene_id)
        if scene.selected_version is None: raise ValueError("An asset version must be selected.")
        updated=scene.model_copy(update={"status":AssetJobStatus.REJECTED}); self._update(updated); return updated
    def metadata(self,scene_id,version):
        path=self.project/"assets"/scene_id/f"version-{int(version):03d}"/"metadata.json"
        if not path.is_file(): raise ValueError("Asset version does not exist.")
        return AssetVersionMetadata.model_validate_json(path.read_text(encoding="utf-8"))
    def preview_path(self,scene_id,version):
        metadata=self.metadata(scene_id,version); return self.project/"assets"/scene_id/f"version-{version:03d}"/metadata.filename,metadata
    def _update(self,updated):
        state=self.state(); scenes=tuple(updated if x.scene_id==updated.scene_id else x for x in state.scenes)
        if not any(x.scene_id==updated.scene_id for x in state.scenes): scenes+= (updated,)
        self._write_json(self.state_path,AssetReviewState(scenes=tuple(sorted(scenes,key=lambda x:x.scene_id))).model_dump(mode="json"),replace=True)
    def _set_global_generated(self):
        repository=WorkflowStateRepository(self.project); state,_=repository.resolve(self.project.name); current=state.stage("assets")
        changed=current.model_copy(update={"status":WorkflowStageStatus.GENERATED,"blocked_reason":None})
        repository.save(WorkflowStateMachine()._state(state.project_id,tuple(changed if x.stage.value=="assets" else x for x in state.stages),state.warnings))
    def _approve_global_if_complete(self):
        required={x.scene_id for x in self.prompts()}; state_assets=self.state()
        if not required or any(state_assets.scene(x).status!=AssetJobStatus.APPROVED for x in required): return
        repository=WorkflowStateRepository(self.project); workflow,_=repository.resolve(self.project.name); current=workflow.stage("assets")
        number=max(current.current_version,1); digest=semantic_sha256(state_assets)
        versions=current.versions or (ArtifactVersion(version=number,artifact_path="assets/state.json",semantic_sha256=digest),)
        changed=current.model_copy(update={"status":WorkflowStageStatus.APPROVED,"current_version":number,
            "selected_version":number,"approved_version":number,"versions":versions,"blocked_reason":None})
        updated=WorkflowStateMachine()._state(workflow.project_id,tuple(changed if x.stage.value=="assets" else x for x in workflow.stages),workflow.warnings)
        repository.save(WorkflowStateMachine().recalculate(updated))
    def _clear_prompt_stale(self,scene_id):
        path=self.project/"workflow"/"asset-staleness.json"
        if not path.is_file(): return
        current=AssetStalenessState.model_validate_json(path.read_text(encoding="utf-8")); self._write_json(path,
            AssetStalenessState(stale_scene_ids=tuple(x for x in current.stale_scene_ids if x!=scene_id)).model_dump(mode="json"),replace=True)
    def _stale_composition(self,scene_id): WorkflowActionService(self.project).execute(self.project.name,"mark_stale","composition",reason=f"asset {scene_id} changed")
    @staticmethod
    def _scene_id(value):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,199}",value): raise ValueError("Scene ID is invalid.")
    @staticmethod
    def _write_json(path,payload,replace=False):
        path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists() and not replace: raise FileExistsError("Asset record already exists.")
        part=path.with_suffix(path.suffix+".part")
        try: part.write_text(json.dumps(payload,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8"); os.replace(part,path)
        finally: part.unlink(missing_ok=True)
    @staticmethod
    def _write_bytes(path,payload):
        with path.open("xb") as stream: stream.write(payload); stream.flush(); os.fsync(stream.fileno())
