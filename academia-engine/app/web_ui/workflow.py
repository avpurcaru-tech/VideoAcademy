"""Deterministic project workflow state and downstream invalidation."""
import json,os
from datetime import datetime,timezone
from enum import Enum
from pathlib import Path

from pydantic import BaseModel,ConfigDict,Field

from app.scene_planning import semantic_sha256

WORKFLOW_SCHEMA_VERSION="1.0"
class WorkflowStageStatus(str,Enum):
    NOT_STARTED="not_started"; BLOCKED="blocked"; READY="ready"; RUNNING="running"; GENERATED="generated"
    APPROVED="approved"; REJECTED="rejected"; STALE="stale"; FAILED="failed"
class WorkflowStage(str,Enum):
    EPISODE="episode"; LYRICS="lyrics"; MUSIC="music"; ALIGNMENT="alignment"; SCENE_PLAN="scene_plan"
    VISUAL_PLAN="visual_plan"; PROMPTS="prompts"; ASSETS="assets"; COMPOSITION="composition"
class WorkflowWarning(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    code:str; stage:WorkflowStage; message:str
class WorkflowDependency(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    upstream:WorkflowStage; downstream:WorkflowStage; requirement:str="approved"
class ArtifactVersion(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    version:int=Field(ge=1); artifact_path:str; semantic_sha256:str=Field(pattern=r"^[a-f0-9]{64}$")
class StageVersionState(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    current_version:int=Field(default=0,ge=0); approved_version:int|None=Field(default=None,ge=1)
    selected_version:int|None=Field(default=None,ge=1); versions:tuple[ArtifactVersion,...]=()
class ApprovalRecord(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    stage:WorkflowStage; version:int=Field(ge=1); reason:str
class WorkflowAction(str,Enum):
    APPROVE="approve"; REJECT="reject"; UNLOCK="unlock"; MARK_GENERATED="mark_generated"
    MARK_FAILED="mark_failed"; MARK_STALE="mark_stale"; SELECT_VERSION="select_version"
class WorkflowActionResult(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    action:WorkflowAction; stage:WorkflowStage; from_status:WorkflowStageStatus; to_status:WorkflowStageStatus
    version:int|None=None; state:"ProjectWorkflowState"
class WorkflowStageState(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    stage:WorkflowStage; status:WorkflowStageStatus; current_version:int=Field(default=0,ge=0)
    approved_version:int|None=Field(default=None,ge=1); blocked_reason:str|None=None; last_error:str|None=None
    selected_version:int|None=Field(default=None,ge=1); versions:tuple[ArtifactVersion,...]=()
class WorkflowTransition(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    stage:WorkflowStage; previous_status:WorkflowStageStatus; new_status:WorkflowStageStatus; reason:str
class ProjectWorkflowState(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    project_id:str; schema_version:str=WORKFLOW_SCHEMA_VERSION; stages:tuple[WorkflowStageState,...]
    dependencies:tuple[WorkflowDependency,...]; warnings:tuple[WorkflowWarning,...]=()
    semantic_sha256:str=Field(pattern=r"^[a-f0-9]{64}$")
    def stage(self,value): return next(x for x in self.stages if x.stage==WorkflowStage(value))

DEPENDENCY_PAIRS=((WorkflowStage.EPISODE,WorkflowStage.LYRICS,"approved"),(WorkflowStage.LYRICS,WorkflowStage.MUSIC,"approved"),
    (WorkflowStage.MUSIC,WorkflowStage.ALIGNMENT,"approved"),(WorkflowStage.ALIGNMENT,WorkflowStage.SCENE_PLAN,"valid"),
    (WorkflowStage.SCENE_PLAN,WorkflowStage.VISUAL_PLAN,"approved"),(WorkflowStage.VISUAL_PLAN,WorkflowStage.PROMPTS,"approved"),
    (WorkflowStage.PROMPTS,WorkflowStage.ASSETS,"approved"),(WorkflowStage.ASSETS,WorkflowStage.COMPOSITION,"approved"))
WORKFLOW_DEPENDENCIES=tuple(WorkflowDependency(upstream=a,downstream=b,requirement=c) for a,b,c in DEPENDENCY_PAIRS)
STAGE_ORDER=tuple(WorkflowStage)

class WorkflowStateMachine:
    def initial(self,project_id):
        states=tuple(WorkflowStageState(stage=x,status=(WorkflowStageStatus.READY if x==WorkflowStage.EPISODE else WorkflowStageStatus.BLOCKED),
            blocked_reason=(None if x==WorkflowStage.EPISODE else "Upstream dependency is not satisfied.")) for x in STAGE_ORDER)
        return self._state(project_id,states)
    def set_status(self,state,stage,status,*,reason="Explicit user transition",last_error=None):
        stage=WorkflowStage(stage); status=WorkflowStageStatus(status); previous=state.stage(stage)
        version=previous.current_version+(1 if status==WorkflowStageStatus.GENERATED else 0)
        approved=version if status==WorkflowStageStatus.APPROVED and version else previous.approved_version
        changed=previous.model_copy(update={"status":status,"current_version":version,"approved_version":approved,
            "blocked_reason":None,"last_error":last_error})
        result=self._replace(state,changed); result=self.recalculate(result)
        return result,WorkflowTransition(stage=stage,previous_status=previous.status,new_status=status,reason=reason)
    def approve(self,state,stage): return self.set_status(state,stage,WorkflowStageStatus.APPROVED,reason="Explicit user approval")
    def change(self,state,stage):
        stage=WorkflowStage(stage); current=state.stage(stage)
        changed=current.model_copy(update={"status":WorkflowStageStatus.GENERATED,"current_version":current.current_version+1,
            "blocked_reason":None,"last_error":None})
        stages=[]; downstream=False
        for value in state.stages:
            if value.stage==stage: stages.append(changed); downstream=True
            elif downstream: stages.append(value.model_copy(update={"status":WorkflowStageStatus.STALE,
                "blocked_reason":f"{stage.value} changed; explicit regeneration is required."}))
            else: stages.append(value)
        return self._state(state.project_id,tuple(stages),state.warnings)
    def perform(self,state,action,stage,*,reason="",version=None,artifact_path=None,artifact_sha256=None):
        action=WorkflowAction(action); stage=WorkflowStage(stage); current=state.stage(stage)
        if action==WorkflowAction.MARK_GENERATED:
            next_version=current.current_version+1; relative=artifact_path or f"{stage.value}/version-{next_version:03d}.json"
            if Path(relative).is_absolute() or ".." in Path(relative).parts: raise ValueError("Artifact path must be project-relative.")
            artifact=ArtifactVersion(version=next_version,artifact_path=relative,
                semantic_sha256=artifact_sha256 or semantic_sha256({"stage":stage.value,"version":next_version,"path":relative}))
            changed=current.model_copy(update={"status":WorkflowStageStatus.GENERATED,"current_version":next_version,
                "selected_version":next_version,"versions":current.versions+(artifact,),"blocked_reason":None,"last_error":None})
            result=self._replace(state,changed); result=self._stale_after(result,stage)
        elif action==WorkflowAction.APPROVE:
            if current.status!=WorkflowStageStatus.GENERATED or current.selected_version is None: raise ValueError("Only a generated version can be approved.")
            changed=current.model_copy(update={"status":WorkflowStageStatus.APPROVED,"approved_version":current.selected_version})
            result=self.recalculate(self._replace(state,changed))
        elif action==WorkflowAction.REJECT:
            if current.status not in {WorkflowStageStatus.GENERATED,WorkflowStageStatus.APPROVED}: raise ValueError("Only generated or approved stages can be rejected.")
            changed=current.model_copy(update={"status":WorkflowStageStatus.REJECTED}); result=self._stale_after(self._replace(state,changed),stage)
        elif action==WorkflowAction.UNLOCK:
            if current.status!=WorkflowStageStatus.APPROVED: raise ValueError("Only an approved stage can be unlocked.")
            changed=current.model_copy(update={"status":WorkflowStageStatus.GENERATED}); result=self._stale_after(self._replace(state,changed),stage)
        elif action==WorkflowAction.SELECT_VERSION:
            if version is None or version not in {x.version for x in current.versions}: raise ValueError("Selected version does not exist.")
            changed=current.model_copy(update={"selected_version":version,"status":WorkflowStageStatus.GENERATED})
            result=self._stale_after(self._replace(state,changed),stage)
        elif action==WorkflowAction.MARK_FAILED:
            changed=current.model_copy(update={"status":WorkflowStageStatus.FAILED,"last_error":reason or "Stage failed."}); result=self._replace(state,changed)
        else:
            changed=current.model_copy(update={"status":WorkflowStageStatus.STALE,"blocked_reason":reason or "Explicitly marked stale."}); result=self._stale_after(self._replace(state,changed),stage)
        final=result.stage(stage)
        return WorkflowActionResult(action=action,stage=stage,from_status=current.status,to_status=final.status,
            version=final.selected_version,state=result)
    def _stale_after(self,state,stage):
        stages=[]; downstream=False
        for value in state.stages:
            if value.stage==stage: stages.append(value); downstream=True
            elif downstream and value.status not in {WorkflowStageStatus.BLOCKED,WorkflowStageStatus.NOT_STARTED}:
                stages.append(value.model_copy(update={"status":WorkflowStageStatus.STALE,
                    "blocked_reason":f"{stage.value} changed; explicit regeneration is required."}))
            else: stages.append(value)
        return self._state(state.project_id,tuple(stages),state.warnings)
    def recalculate(self,state):
        values={x.stage:x for x in state.stages}
        for dependency in WORKFLOW_DEPENDENCIES:
            upstream=values[dependency.upstream]; downstream=values[dependency.downstream]
            satisfied=(upstream.status==WorkflowStageStatus.APPROVED or
                (dependency.requirement=="valid" and upstream.status in {WorkflowStageStatus.GENERATED,WorkflowStageStatus.APPROVED}))
            if satisfied and downstream.status in {WorkflowStageStatus.BLOCKED,WorkflowStageStatus.NOT_STARTED}:
                values[dependency.downstream]=downstream.model_copy(update={"status":WorkflowStageStatus.READY,"blocked_reason":None})
        return self._state(state.project_id,tuple(values[x] for x in STAGE_ORDER),state.warnings)
    def _replace(self,state,changed): return self._state(state.project_id,tuple(changed if x.stage==changed.stage else x for x in state.stages),state.warnings)
    @staticmethod
    def _state(project_id,stages,warnings=()):
        core={"project_id":project_id,"schema_version":WORKFLOW_SCHEMA_VERSION,"stages":[x.model_dump(mode="json") for x in stages],
            "dependencies":[x.model_dump(mode="json") for x in WORKFLOW_DEPENDENCIES],"warnings":[x.model_dump(mode="json") for x in warnings]}
        return ProjectWorkflowState(**core,semantic_sha256=semantic_sha256(core))

def write_workflow_state(path,state):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True); part=path.with_suffix(path.suffix+".part")
    try:
        part.write_text(json.dumps(ProjectWorkflowState.model_validate(state).model_dump(mode="json"),ensure_ascii=False,
            sort_keys=True,indent=2)+"\n",encoding="utf-8")
        with part.open("r+b") as stream: os.fsync(stream.fileno())
        os.replace(part,path)
    finally: part.unlink(missing_ok=True)
def read_workflow_state(path): return ProjectWorkflowState.model_validate_json(Path(path).read_text(encoding="utf-8"))
class WorkflowStateRepository:
    def __init__(self,project_directory): self.path=Path(project_directory)/"workflow"/"state.json"
    def resolve(self,project_id):
        if self.path.is_file(): return read_workflow_state(self.path),True
        value=WorkflowStateMachine().initial(project_id); write_workflow_state(self.path,value); return value,False
    def save(self,state): write_workflow_state(self.path,state)

class WorkflowActionService:
    def __init__(self,project_directory):
        self.project_directory=Path(project_directory); self.repository=WorkflowStateRepository(self.project_directory)
        self.history_path=self.project_directory/"workflow"/"history.jsonl"
    def execute(self,project_id,action,stage,*,reason="",version=None,artifact_path=None,artifact_sha256=None):
        state,_reused=self.repository.resolve(project_id); result=WorkflowStateMachine().perform(state,action,stage,
            reason=reason,version=version,artifact_path=artifact_path,artifact_sha256=artifact_sha256)
        self.repository.save(result.state); self._audit(result,reason); return result
    def _audit(self,result,reason):
        self.history_path.parent.mkdir(parents=True,exist_ok=True)
        record={"action":result.action.value,"stage":result.stage.value,"from_status":result.from_status.value,
            "to_status":result.to_status.value,"version":result.version,"reason":reason,"timestamp":datetime.now(timezone.utc).isoformat()}
        with self.history_path.open("a",encoding="utf-8",newline="\n") as stream:
            stream.write(json.dumps(record,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n"); stream.flush(); os.fsync(stream.fileno())

WorkflowActionResult.model_rebuild()
