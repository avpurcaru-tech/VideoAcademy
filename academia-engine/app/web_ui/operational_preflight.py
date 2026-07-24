"""Strictly read-only operational readiness checks."""
import hashlib,json,os,platform,shutil,sys
from dataclasses import asdict,dataclass
from enum import Enum
from pathlib import Path

from .job_recovery import ExternalJobRepository,ExternalJobStatus
from .workflow import ProjectWorkflowState,WorkflowStageStatus

class PreflightSeverity(str,Enum): INFO="info"; WARNING="warning"; ERROR="error"; BLOCKING="blocking"
class PreflightStatus(str,Enum): READY="ready"; READY_WITH_WARNINGS="ready_with_warnings"; NOT_READY="not_ready"

@dataclass(frozen=True)
class OperationalPreflightCheck:
    check_id:str; component:str; description:str
@dataclass(frozen=True)
class OperationalPreflightFinding:
    check_id:str; severity:PreflightSeverity; component:str; message:str; remediation:str; artifact_path:str|None=None
@dataclass(frozen=True)
class OperationalPreflightReport:
    runtime_mode:str; projects_root:str; server:str; project_id:str|None; findings:tuple[OperationalPreflightFinding,...]
    external_http_calls:int=0; ai_generation_calls:int=0; ffmpeg_calls:int=0; write_operations:int=0
    @property
    def status(self):
        if any(x.severity==PreflightSeverity.BLOCKING for x in self.findings): return PreflightStatus.NOT_READY
        if any(x.severity in {PreflightSeverity.WARNING,PreflightSeverity.ERROR} for x in self.findings): return PreflightStatus.READY_WITH_WARNINGS
        return PreflightStatus.READY
    @property
    def ready(self): return self.status!=PreflightStatus.NOT_READY
    def to_dict(self):
        value=asdict(self); value["status"]=self.status.value
        for finding in value["findings"]: finding["severity"]=finding["severity"].value
        return value
    def to_json(self): return json.dumps(self.to_dict(),ensure_ascii=False,sort_keys=True,indent=2)+"\n"
    def to_text(self):
        lines=["Operational Preflight",f"Runtime: {self.runtime_mode}",f"Projects root: {self.projects_root}",f"Server: {self.server}",""]
        lines.extend(f"[{x.severity.value.upper()}] {x.check_id}: {x.message} Remediation: {x.remediation}" for x in self.findings)
        lines.extend(("",f"Project readiness: {self.status.value.upper()}",f"External HTTP calls: {self.external_http_calls}",f"AI generation calls: {self.ai_generation_calls}",f"FFmpeg calls: {self.ffmpeg_calls}",f"Write operations: {self.write_operations}"))
        return "\n".join(lines)+"\n"

class ConnectivityConfirmationRequired(ValueError): pass

class OperationalPreflightService:
    def __init__(self,settings,*,config_path=None,connectivity_checkers=None):
        self.settings=settings; self.config_path=Path(config_path) if config_path else None; self.connectivity_checkers=connectivity_checkers or {}
    def run(self,project_id=None,*,check_provider_connectivity=False,confirm_connectivity=False):
        if check_provider_connectivity and not confirm_connectivity: raise ConnectivityConfirmationRequired("Explicit confirmation is required for external provider connectivity checks.")
        findings=[]; add=lambda i,s,c,m,r,p=None: findings.append(OperationalPreflightFinding(i,PreflightSeverity(s),c,m,r,p))
        self._local(add); self._providers(add,check_provider_connectivity)
        if project_id: self._project(project_id,add)
        else: add("project.scope","info","project","No project-specific checks requested.","Pass --project-id to check one project.")
        calls=sum(1 for name in self.connectivity_checkers if check_provider_connectivity and self._provider_enabled(name))
        return OperationalPreflightReport(self.settings.runtime_mode.value,str(self.settings.projects_root),f"{self.settings.server.host}:{self.settings.server.port}",project_id,tuple(findings),external_http_calls=calls)
    def _local(self,add):
        add("runtime.python","info","runtime",platform.python_version(),"Use a supported Python version.")
        add("runtime.platform","info","runtime",platform.platform(),"No action required.")
        root=self.settings.projects_root
        add("projects.root","info" if root.is_dir() else "blocking","filesystem",f"Projects root {'exists' if root.is_dir() else 'is missing'}.","Create/configure the projects root.",str(root))
        if root.exists():
            add("projects.readable","info" if os.access(root,os.R_OK) else "blocking","filesystem",f"Projects root is {'readable' if os.access(root,os.R_OK) else 'not readable'}.","Grant local read permission.",str(root))
            add("projects.writable","info" if os.access(root,os.W_OK) else "blocking","filesystem",f"Projects root is {'writable' if os.access(root,os.W_OK) else 'not writable'}.","Grant local write permission.",str(root))
            free=shutil.disk_usage(root).free; add("disk.free","warning" if free<1024**3 else "info","filesystem",f"Free space: {free} bytes.","Free disk space before paid generation.")
        if self.config_path: add("config.file","info" if self.config_path.is_file() else "warning","configuration",f"Config file {'found' if self.config_path.is_file() else 'not found'}.","Verify --config path.",str(self.config_path))
        else: add("config.file","info","configuration","Defaults/environment configuration in use.","Use --config for a durable local configuration.")
        add("runtime.mode","info","configuration",f"Runtime mode: {self.settings.runtime_mode.value}.","Select production only when providers are reviewed.")
        loopback=self.settings.server.host in {"127.0.0.1","localhost","::1"}; add("server.loopback","info" if loopback else "blocking","server",f"Server host: {self.settings.server.host}.","Bind the local UI to loopback.")
        for executable,required in ((self.settings.ffmpeg.executable,self.settings.ffmpeg.enabled),("ffprobe",self.settings.ffmpeg.enabled)):
            found=shutil.which(executable); add(f"binary.{Path(executable).name}","info" if found or not required else "blocking","media",f"{executable}: {'available' if found else 'not found'}.",f"Install/configure {executable}.",found)
        ui=Path(__file__).parent; required=(ui/"static"/"styles.css",ui/"static"/"app.js")
        for path in required: add(f"file.{path.name}","info" if path.is_file() else "blocking","ui",f"{path.name}: {'available' if path.is_file() else 'missing'}.","Restore the packaged UI file.",str(path))
    def _providers(self,add,connectivity):
        providers=(("lyrics",self.settings.lyrics.enabled,bool(self.settings.lyrics.api_key),self.settings.lyrics.provider),("suno",self.settings.suno.enabled,bool(self.settings.suno.api_key),"sunoapi.org"),("assets",self.settings.assets.enabled,bool(self.settings.assets.api_key),self.settings.assets.provider))
        for key,enabled,configured,name in providers:
            if self.settings.runtime_mode.value=="dry_run": severity,message="info",f"{name}: dry-run."
            elif not enabled: severity,message="warning",f"{name}: disabled; {'configured' if configured else 'not configured'}."
            elif not configured: severity,message="blocking",f"{name}: not configured."
            else: severity,message="info",f"{name}: configured; secret present."
            add(f"provider.{key}",severity,"provider",message,"Enable and configure the provider only when required.")
            if connectivity and enabled:
                checker=self.connectivity_checkers.get(key)
                if checker is None: add(f"provider.{key}.connectivity","warning","provider","No safe read-only health endpoint is configured.","Verify provider documentation manually.")
                else:
                    try: ok,message=checker(); add(f"provider.{key}.connectivity","info" if ok else "warning","provider",str(message),"Review provider availability.")
                    except Exception as error: add(f"provider.{key}.connectivity","warning","provider",f"Connectivity check failed: {type(error).__name__}.","Check network and provider status.")
    def _provider_enabled(self,name): return {"lyrics":self.settings.lyrics.enabled,"suno":self.settings.suno.enabled,"assets":self.settings.assets.enabled}.get(name,False)
    def _project(self,project_id,add):
        if not project_id or any(x not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for x in project_id): add("project.id","blocking","project","Invalid project ID.","Use an existing safe project ID."); return
        project=self.settings.projects_root/project_id; manifest=project/"project.json"; workflow=project/"workflow"/"state.json"
        self._json_file(manifest,"project.manifest","project",add,blocking=True)
        state=None
        try: state=ProjectWorkflowState.model_validate_json(workflow.read_text(encoding="utf-8")); add("project.workflow","info","workflow","Workflow state is valid.","No action required.",str(workflow))
        except Exception: add("project.workflow","blocking","workflow","Workflow state is missing or invalid.","Restore a valid workflow/state.json.",str(workflow))
        if state:
            for stage_name in ("lyrics","music","alignment","scene_plan","visual_plan","prompts","assets"):
                stage=state.stage(stage_name); approved=stage.status==WorkflowStageStatus.APPROVED and stage.approved_version is not None
                add(f"project.{stage_name}.approved","info" if approved else "blocking",stage_name,f"{stage_name}: {stage.status.value}.",f"Generate, review and approve {stage_name}.")
                if stage.status==WorkflowStageStatus.STALE: add(f"project.{stage_name}.stale","blocking",stage_name,f"{stage_name} contains stale artifacts.",f"Explicitly rebuild and approve {stage_name}.")
                if approved:
                    artifact=next((x for x in stage.versions if x.version==stage.approved_version),None)
                    if artifact:
                        path=project/artifact.artifact_path; add(f"project.{stage_name}.artifact","info" if path.is_file() else "blocking",stage_name,f"Approved artifact {'exists' if path.is_file() else 'is missing'}.","Restore the approved artifact.",str(path))
            self._music(project,state,add)
            composition=state.stage("composition"); add("project.composition.readiness","info" if composition.status in {WorkflowStageStatus.READY,WorkflowStageStatus.GENERATED,WorkflowStageStatus.APPROVED} else "warning","composition",f"Composition status: {composition.status.value}.","Complete all upstream approvals and run composition preflight.")
        jobs=ExternalJobRepository(self.settings.projects_root).list(project_id); interrupted=[x for x in jobs if x.status in {ExternalJobStatus.SUBMITTED,ExternalJobStatus.PROCESSING,ExternalJobStatus.UNKNOWN,ExternalJobStatus.RECOVERY_REQUIRED}]
        add("project.interrupted_jobs","warning" if interrupted else "info","jobs",f"Interrupted jobs: {len(interrupted)}.","Open the Jobs page and recover explicitly.")
        edl=project/"composition"/"preflight.json"; add("project.edl","info" if edl.is_file() else "warning","composition",f"Composition EDL/preflight {'exists' if edl.is_file() else 'not persisted'}.","Run the read-only composition review when dependencies are approved.",str(edl))
    @staticmethod
    def _json_file(path,check_id,component,add,blocking=False):
        try: json.loads(path.read_text(encoding="utf-8")); ok=True
        except Exception: ok=False
        add(check_id,"info" if ok else ("blocking" if blocking else "warning"),component,f"{path.name} is {'valid' if ok else 'missing or invalid'}.",f"Restore a valid {path.name}.",str(path))
    @staticmethod
    def _music(project,state,add):
        stage=state.stage("music")
        if stage.approved_version is None: add("project.music.metadata","blocking","music","Approved music metadata is missing.","Select and approve a music variant."); return
        job=project/"music"/f"version-{stage.approved_version:03d}"/"job.json"
        try:
            data=json.loads(job.read_text(encoding="utf-8")); variant=data.get("approved_variant_id") or data.get("approvedVariantId"); item=next(x for x in data.get("variants",[]) if x.get("variant_id",x.get("variantId"))==variant); audio=job.parent/f"{variant}.mp3"; actual=hashlib.sha256(audio.read_bytes()).hexdigest(); expected=item.get("sha256"); ok=actual==expected
            add("project.music.audio","info" if ok else "blocking","music",f"Approved audio {'exists and SHA matches' if ok else 'SHA mismatch'}.","Restore/redownload the approved variant.",str(audio))
        except Exception: add("project.music.metadata","blocking","music","Approved music metadata or audio is missing.","Restore the approved job metadata and audio.",str(job))
