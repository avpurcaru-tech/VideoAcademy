"""Explicit versioned planning/review stages for the local UI (Sprint 18.6)."""
import json,os
from pathlib import Path
from typing import Any,Protocol

from pydantic import BaseModel,ConfigDict,Field

from app.scene_planning import semantic_sha256
from .workflow import (ArtifactVersion,WorkflowActionService,WorkflowStage,WorkflowStageStatus,
    WorkflowStateMachine,WorkflowStateRepository)

class PlanningReviewError(RuntimeError): pass
class PlanningStageBlocked(PlanningReviewError): pass
class PlanningBuilder(Protocol):
    def build(self,context:dict[str,Any])->"PlanningBuildResult": ...
class PlanningBuildResult(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    data:dict[str,Any]; warnings:tuple[str,...]=(); review_required:bool=False; provider_metadata:dict[str,Any]=Field(default_factory=dict)
class PlanningArtifactVersion(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    stage:WorkflowStage; version:int=Field(ge=1); status:str="generated"; data:dict[str,Any]
    warnings:tuple[str,...]=(); review_required:bool=False; dependency_sha256:str=Field(pattern=r"^[a-f0-9]{64}$")
    semantic_sha256:str=Field(pattern=r"^[a-f0-9]{64}$"); provider_metadata:dict[str,Any]=Field(default_factory=dict)
class PromptReviewScene(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    scene_id:str; positive_prompt:str; negative_prompt:str=""; structured_parameters:dict[str,Any]=Field(default_factory=dict)
class PromptReviewBundle(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    version:int=Field(ge=1); prompts:tuple[PromptReviewScene,...]; dependency_sha256:str=Field(pattern=r"^[a-f0-9]{64}$")
    semantic_sha256:str=Field(pattern=r"^[a-f0-9]{64}$")
class PromptSceneOverride(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    scene_id:str; version:int=Field(ge=1); source_bundle_version:int=Field(ge=1); positive_prompt:str
    negative_prompt:str=""; structured_parameters:dict[str,Any]=Field(default_factory=dict); feedback:str|None=None
class AssetStalenessState(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    stale_scene_ids:tuple[str,...]=()

STAGE_LAYOUT={WorkflowStage.ALIGNMENT:("alignment","alignment"),WorkflowStage.SCENE_PLAN:("visual/scene-plans","scene_plan"),
    WorkflowStage.VISUAL_PLAN:("visual/plans","visual_plan"),WorkflowStage.PROMPTS:("visual/prompts","prompts")}
UPSTREAM={WorkflowStage.ALIGNMENT:WorkflowStage.MUSIC,WorkflowStage.SCENE_PLAN:WorkflowStage.ALIGNMENT,
    WorkflowStage.VISUAL_PLAN:WorkflowStage.SCENE_PLAN,WorkflowStage.PROMPTS:WorkflowStage.VISUAL_PLAN}

class PlanningReviewService:
    def __init__(self,project_directory,builders=None): self.project=Path(project_directory); self.builders=builders or {}
    def versions(self,stage):
        stage=WorkflowStage(stage); directory=self.project/STAGE_LAYOUT[stage][0]
        model=PromptReviewBundle if stage==WorkflowStage.PROMPTS else PlanningArtifactVersion
        return tuple(model.model_validate_json(x.read_text(encoding="utf-8")) for x in sorted(directory.glob("version-*.json")))
    def build(self,stage,*,rebuild=False):
        stage=WorkflowStage(stage); state,_=WorkflowStateRepository(self.project).resolve(self.project.name); upstream=state.stage(UPSTREAM[stage])
        if upstream.status!=WorkflowStageStatus.APPROVED: raise PlanningStageBlocked(f"{UPSTREAM[stage].value} must be approved first.")
        builder=self.builders.get(stage.value)
        if builder is None: raise PlanningReviewError(f"No {stage.value} builder is configured.")
        context=self._context(stage,state); result=PlanningBuildResult.model_validate(builder.build(context)); number=state.stage(stage).current_version+1
        if stage==WorkflowStage.PROMPTS:
            prompts=tuple(PromptReviewScene.model_validate(x) for x in result.data.get("prompts",()))
            core={"version":number,"prompts":[x.model_dump(mode="json") for x in prompts],"dependency_sha256":semantic_sha256(context)}
            value=PromptReviewBundle(**core,semantic_sha256=semantic_sha256(core))
        else:
            core={"stage":stage.value,"version":number,"status":"generated","data":result.data,"warnings":list(result.warnings),
                "review_required":result.review_required,"dependency_sha256":semantic_sha256(context),"provider_metadata":result.provider_metadata}
            value=PlanningArtifactVersion(**core,semantic_sha256=semantic_sha256(core))
        path=self.project/STAGE_LAYOUT[stage][0]/f"version-{number:03d}.json"; self._write(path,value)
        WorkflowActionService(self.project).execute(self.project.name,"mark_generated",stage,reason=("rebuild" if rebuild else "build"),
            artifact_path=str(path.relative_to(self.project)).replace("\\","/"),artifact_sha256=value.semantic_sha256)
        return value
    def selected(self,stage):
        values={x.version:x for x in self.versions(stage)}; state,_=WorkflowStateRepository(self.project).resolve(self.project.name)
        selected=state.stage(stage).selected_version
        return values.get(selected) or (values[max(values)] if values else None)
    def effective_prompts(self):
        bundle=self.selected(WorkflowStage.PROMPTS)
        if bundle is None: return ()
        result=[]
        for prompt in bundle.prompts:
            overrides=sorted((self.project/"visual"/"prompts"/"overrides"/prompt.scene_id).glob("version-*.json"))
            result.append(PromptSceneOverride.model_validate_json(overrides[-1].read_text(encoding="utf-8")) if overrides else prompt)
        return tuple(result)
    def edit_prompt(self,scene_id,positive_prompt,negative_prompt="",structured_parameters=None,feedback=None):
        bundle=self.selected(WorkflowStage.PROMPTS)
        if bundle is None: raise PlanningReviewError("Prompt bundle does not exist.")
        source=next((x for x in bundle.prompts if x.scene_id==scene_id),None)
        if source is None: raise ValueError("Prompt scene does not exist.")
        directory=self.project/"visual"/"prompts"/"overrides"/scene_id; number=len(tuple(directory.glob("version-*.json")))+1
        value=PromptSceneOverride(scene_id=scene_id,version=number,source_bundle_version=bundle.version,
            positive_prompt=positive_prompt.strip() or source.positive_prompt,negative_prompt=negative_prompt.strip(),
            structured_parameters=structured_parameters or source.structured_parameters,feedback=(feedback.strip() or None) if feedback else None)
        self._write(directory/f"version-{number:03d}.json",value); self._record_prompt_override(bundle,value); return value
    def regenerate_prompt(self,scene_id,feedback=None):
        builder=self.builders.get("prompt_scene")
        if builder is None: raise PlanningReviewError("No prompt scene builder is configured.")
        bundle=self.selected(WorkflowStage.PROMPTS); source=next((x for x in bundle.prompts if x.scene_id==scene_id),None) if bundle else None
        if source is None: raise ValueError("Prompt scene does not exist.")
        result=PlanningBuildResult.model_validate(builder.build({"scene":source.model_dump(mode="json"),"feedback":feedback}))
        prompt=PromptReviewScene.model_validate(result.data); return self.edit_prompt(scene_id,prompt.positive_prompt,prompt.negative_prompt,prompt.structured_parameters,feedback)
    def _record_prompt_override(self,bundle,override):
        repository=WorkflowStateRepository(self.project); state,_=repository.resolve(self.project.name); current=state.stage("prompts")
        number=current.current_version+1; artifact=ArtifactVersion(version=number,
            artifact_path=f"visual/prompts/overrides/{override.scene_id}/version-{override.version:03d}.json",semantic_sha256=semantic_sha256(override))
        changed=current.model_copy(update={"status":WorkflowStageStatus.GENERATED,"current_version":number,"selected_version":number,
            "versions":current.versions+(artifact,)})
        stages=tuple(changed if x.stage==WorkflowStage.PROMPTS else x for x in state.stages); repository.save(WorkflowStateMachine()._state(state.project_id,stages,state.warnings))
        self._mark_asset_stale(override.scene_id); WorkflowActionService(self.project).execute(self.project.name,"mark_stale","composition",reason=f"prompt {override.scene_id} changed")
    def _mark_asset_stale(self,scene_id):
        path=self.project/"workflow"/"asset-staleness.json"; current=AssetStalenessState.model_validate_json(path.read_text(encoding="utf-8")) if path.is_file() else AssetStalenessState()
        value=AssetStalenessState(stale_scene_ids=tuple(sorted(set(current.stale_scene_ids)|{scene_id}))); self._write(path,value,replace=True)
    def _context(self,stage,state):
        upstream=state.stage(UPSTREAM[stage]); return {"project_id":self.project.name,"stage":stage.value,
            "upstream_stage":upstream.stage.value,"upstream_version":upstream.approved_version}
    @staticmethod
    def _write(path,value,replace=False):
        path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists() and not replace: raise FileExistsError("Planning version already exists.")
        part=path.with_suffix(path.suffix+".part")
        try:
            part.write_text(json.dumps(value.model_dump(mode="json"),ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8")
            with part.open("r+b") as stream: os.fsync(stream.fileno())
            os.replace(part,path)
        finally: part.unlink(missing_ok=True)
