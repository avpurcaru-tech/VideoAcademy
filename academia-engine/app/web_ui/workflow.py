"""Deterministic project workflow state and downstream invalidation."""
import json,os
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
class WorkflowStageState(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    stage:WorkflowStage; status:WorkflowStageStatus; current_version:int=Field(default=0,ge=0)
    approved_version:int|None=Field(default=None,ge=1); blocked_reason:str|None=None; last_error:str|None=None
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
