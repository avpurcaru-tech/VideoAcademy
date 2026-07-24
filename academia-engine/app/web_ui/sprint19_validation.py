"""Safe first-real-project validation primitives; never runs a paid pipeline."""
import hashlib,json,os
from dataclasses import asdict,dataclass
from enum import Enum
from pathlib import Path

from .operational_preflight import OperationalPreflightService

@dataclass(frozen=True)
class CostConfirmation:
    provider:str; action:str; project_id:str; confirmed:bool; estimated_cost_label:str|None=None
    def require(self):
        if not self.confirmed: raise CostConfirmationRequired(f"Separate confirmation required for {self.provider}/{self.action}. Cost unknown — this action may consume credits.")
        return self
class CostConfirmationRequired(ValueError): pass
class SmokeTestMode(str,Enum): DRY_RUN="dry-run"; OPERATOR_CONFIRMED="operator-confirmed"

@dataclass(frozen=True)
class Sprint19ValidationReport:
    mode:SmokeTestMode; project_id:str|None; operational_status:str; checks:tuple[str,...]
    external_http_calls:int=0; ai_generation_calls:int=0; ffmpeg_calls:int=0; paid_calls:int=0; write_operations:int=0
    def to_dict(self):
        value=asdict(self); value["mode"]=self.mode.value; return value
    def to_json(self): return json.dumps(self.to_dict(),ensure_ascii=False,sort_keys=True,indent=2)+"\n"
    def to_text(self):
        return ("Sprint 19 Validation\n"+f"Mode: {self.mode.value}\nProject: {self.project_id or 'not selected'}\nOperational status: {self.operational_status}\n"+
            "\n".join(f"- {x}" for x in self.checks)+f"\nExternal HTTP calls: {self.external_http_calls}\nAI generation calls: {self.ai_generation_calls}\nFFmpeg calls: {self.ffmpeg_calls}\nPaid calls: {self.paid_calls}\nWrite operations: {self.write_operations}\n")

class RealProjectSmokeTest:
    STAGES=("lyrics","music","alignment","scene_plan","visual_plan","prompts","assets","composition")
    def __init__(self,settings,*,config_path=None): self.settings=settings; self.config_path=config_path
    def run_dry(self,project_id=None,include_provider_connectivity=False):
        report=OperationalPreflightService(self.settings,config_path=self.config_path).run(project_id,check_provider_connectivity=include_provider_connectivity,confirm_connectivity=include_provider_connectivity)
        checks=("dependency injection configured","requests can be constructed only at explicit stage actions","separate cost confirmation required per external stage","no full-pipeline action available")
        return Sprint19ValidationReport(SmokeTestMode.DRY_RUN,project_id,report.status.value,checks,external_http_calls=report.external_http_calls)
    def confirm_stage(self,confirmation):
        if confirmation.action not in self.STAGES: raise ValueError("Unknown workflow stage action.")
        return confirmation.require()

@dataclass(frozen=True)
class ApprovalCheckpoint:
    stage:str; approved_version:int; dependency_hashes:dict[str,str]; artifact_paths:tuple[str,...]; validation_summary:str

class ApprovalCheckpointService:
    def __init__(self,project): self.project=Path(project); self.directory=self.project/"workflow"/"checkpoints"
    def persist(self,state,stage):
        current=state.stage(stage)
        if current.approved_version is None: raise ValueError("An approved version is required for a checkpoint.")
        dependencies={x.stage.value:self._stage_hash(x) for x in state.stages if x.stage.value!=stage and x.approved_version is not None}
        paths=tuple(x.artifact_path for x in current.versions if x.version==current.approved_version)
        checkpoint=ApprovalCheckpoint(stage.value if hasattr(stage,"value") else str(stage),current.approved_version,dependencies,paths,"approved artifact and workflow dependency snapshot validated")
        name=checkpoint.stage.replace("_","-")+"-approved.json"; path=self.directory/name; path.parent.mkdir(parents=True,exist_ok=True); part=path.with_suffix(".json.part")
        try:
            part.write_text(json.dumps(asdict(checkpoint),ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8")
            with part.open("r+b") as stream: os.fsync(stream.fileno())
            os.replace(part,path)
        finally: part.unlink(missing_ok=True)
        return path
    @staticmethod
    def _stage_hash(stage):
        core={"stage":stage.stage.value,"approved_version":stage.approved_version,"artifacts":[{"version":x.version,"path":x.artifact_path,"sha256":x.semantic_sha256} for x in stage.versions]}
        return hashlib.sha256(json.dumps(core,sort_keys=True,separators=(",",":")).encode()).hexdigest()
