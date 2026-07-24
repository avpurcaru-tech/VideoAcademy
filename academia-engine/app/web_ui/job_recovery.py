"""Durable, user-driven recovery for interrupted local/external jobs."""
import hashlib,json,os,re
from dataclasses import asdict,dataclass,replace
from enum import Enum
from pathlib import Path

class ExternalJobStatus(str,Enum):
    CREATED="created"; SUBMITTED="submitted"; PROCESSING="processing"; COMPLETED="completed"; FAILED="failed"; CANCELLED="cancelled"; UNKNOWN="unknown"; RECOVERY_REQUIRED="recovery_required"
class ExternalJobKind(str,Enum):
    LYRICS="lyrics"; MUSIC="music"; ALIGNMENT="alignment"; ASSET="asset"; COMPOSITION="composition"

@dataclass(frozen=True)
class ExternalJobRecord:
    job_id:str; project_id:str; stage:str; kind:ExternalJobKind; provider:str; provider_job_id:str|None
    request_sha256:str; request_artifact_path:str; status:ExternalJobStatus; attempt:int=1
    last_known_provider_status:str|None=None; result_artifact_path:str|None=None; error_code:str|None=None; error_message:str|None=None
    idempotency_key:str|None=None; scene_id:str|None=None; variant_downloads:dict|None=None; duplicate_cost_warning:bool=False
    @classmethod
    def create(cls,*,project_id,stage,kind,provider,request_artifact_path,request_sha256,provider_job_id=None,scene_id=None,idempotency_key=None,status=ExternalJobStatus.CREATED,result_artifact_path=None,variant_downloads=None):
        seed=f"{project_id}\0{stage}\0{request_sha256}\0{scene_id or ''}"; job_id=f"job-{hashlib.sha256(seed.encode()).hexdigest()[:20]}"
        return cls(job_id,project_id,stage,ExternalJobKind(kind),provider,provider_job_id,request_sha256,request_artifact_path,ExternalJobStatus(status),result_artifact_path=result_artifact_path,idempotency_key=idempotency_key,scene_id=scene_id,variant_downloads=variant_downloads)

@dataclass(frozen=True)
class RecoveryReport:
    jobs:tuple[ExternalJobRecord,...]
    @property
    def incomplete(self): return tuple(x for x in self.jobs if x.status in {ExternalJobStatus.SUBMITTED,ExternalJobStatus.PROCESSING,ExternalJobStatus.UNKNOWN,ExternalJobStatus.RECOVERY_REQUIRED})

class JobConfirmationRequired(ValueError): pass
class DuplicateCostWarningRequired(JobConfirmationRequired): pass
class JobNotFound(KeyError): pass

class ExternalJobRepository:
    def __init__(self,projects_root): self.root=Path(projects_root)
    def save(self,record):
        path=self.path(record.project_id,record.job_id); path.parent.mkdir(parents=True,exist_ok=True); part=path.with_suffix(".json.part")
        payload=asdict(record); payload["kind"]=record.kind.value; payload["status"]=record.status.value
        try:
            part.write_text(json.dumps(payload,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8")
            with part.open("r+b") as stream: os.fsync(stream.fileno())
            os.replace(part,path)
        finally: part.unlink(missing_ok=True)
        return record
    def load(self,job_id):
        for path in self.root.glob("*/workflow/jobs/*.json"):
            if path.stem==job_id: return self._read(path)
        raise JobNotFound(job_id)
    def list(self,project_id=None):
        pattern=f"{project_id}/workflow/jobs/*.json" if project_id else "*/workflow/jobs/*.json"
        return tuple(sorted((self._read(x) for x in self.root.glob(pattern)),key=lambda x:(x.project_id,x.job_id)))
    def path(self,project_id,job_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]+",project_id) or not re.fullmatch(r"job-[a-f0-9]{20}",job_id): raise ValueError("invalid job identity")
        return self.root/project_id/"workflow"/"jobs"/f"{job_id}.json"
    @staticmethod
    def _read(path):
        value=json.loads(path.read_text(encoding="utf-8")); value["kind"]=ExternalJobKind(value["kind"]); value["status"]=ExternalJobStatus(value["status"]); return ExternalJobRecord(**value)

class JobRecoveryService:
    def __init__(self,projects_root,*,refreshers=None,resumers=None,idempotent_providers=()):
        self.repository=ExternalJobRepository(projects_root); self.refreshers=refreshers or {}; self.resumers=resumers or {}; self.idempotent_providers=set(idempotent_providers)
    def scan(self,project_id=None):
        jobs=[]
        for job in self.repository.list(project_id):
            if job.kind==ExternalJobKind.COMPOSITION and job.status==ExternalJobStatus.COMPLETED:
                target=self.repository.root/job.project_id/(job.result_artifact_path or "")
                if not job.result_artifact_path or target.suffix==".part" or not target.is_file(): job=replace(job,status=ExternalJobStatus.RECOVERY_REQUIRED,error_code="partial_output",error_message="Render output is incomplete.")
            jobs.append(job)
        return RecoveryReport(tuple(jobs))
    def refresh_job(self,job_id,*,confirm_external_check):
        if not confirm_external_check: raise JobConfirmationRequired("Explicit confirmation is required to check provider status.")
        job=self.repository.load(job_id)
        if not job.provider_job_id: return self.repository.save(replace(job,status=ExternalJobStatus.RECOVERY_REQUIRED,error_code="provider_job_id_missing",error_message="External provider job ID is missing."))
        callback=self.refreshers.get(job.provider) or self.refreshers.get(job.kind.value)
        if callback is None: return self.repository.save(replace(job,status=ExternalJobStatus.RECOVERY_REQUIRED,error_code="refresh_adapter_unavailable",error_message="No provider refresh adapter is configured."))
        updated=callback(job); return self.repository.save(self._preserve_identity(job,updated))
    def resume_job(self,job_id,*,confirm_resume):
        if not confirm_resume: raise JobConfirmationRequired("Explicit confirmation is required to resume a job.")
        job=self.repository.load(job_id)
        if not job.provider_job_id and job.provider not in self.idempotent_providers and not job.duplicate_cost_warning:
            warning=replace(job,duplicate_cost_warning=True,status=ExternalJobStatus.RECOVERY_REQUIRED,error_code="duplicate_cost_warning",error_message="Provider has no idempotency guarantee; resubmission may duplicate cost."); self.repository.save(warning); raise DuplicateCostWarningRequired(warning.error_message)
        callback=self.resumers.get(job.provider) or self.resumers.get(job.kind.value)
        if callback is None: return self.repository.save(replace(job,status=ExternalJobStatus.RECOVERY_REQUIRED,error_code="resume_adapter_unavailable",error_message="No provider resume adapter is configured."))
        updated=callback(job); return self.repository.save(self._preserve_identity(job,updated))
    def mark_failed(self,job_id,reason="Marked failed by user."):
        job=self.repository.load(job_id); return self.repository.save(replace(job,status=ExternalJobStatus.FAILED,error_code="user_marked_failed",error_message=reason))
    def abandon(self,job_id):
        job=self.repository.load(job_id); return self.repository.save(replace(job,status=ExternalJobStatus.CANCELLED,error_code="user_abandoned",error_message="Job abandoned by user."))
    @staticmethod
    def _preserve_identity(original,updated):
        if not isinstance(updated,ExternalJobRecord): raise TypeError("recovery adapter must return ExternalJobRecord")
        return replace(updated,job_id=original.job_id,project_id=original.project_id,provider_job_id=updated.provider_job_id or original.provider_job_id,request_sha256=original.request_sha256,request_artifact_path=original.request_artifact_path,scene_id=original.scene_id)
