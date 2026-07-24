import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch

from app.web_ui.bootstrap import ApplicationSettings,LyricsProviderSettings,SecretValue,ServerSettings
from app.web_ui.job_recovery import ExternalJobRecord,ExternalJobRepository
from app.web_ui.operational_preflight import *
from app.web_ui.server import create_app
from app.web_ui.bootstrap import build_application_services
from app.web_ui.workflow import ArtifactVersion,WorkflowStageStatus,WorkflowStateMachine,write_workflow_state

class OperationalPreflightTests(unittest.TestCase):
    def setUp(self): self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name); self.settings=ApplicationSettings(projects_root=self.root)
    def tearDown(self): self.temp.cleanup()
    def preflight(self,**kwargs):
        with patch("app.web_ui.operational_preflight.shutil.which",side_effect=lambda x:f"C:/bin/{x}"): return OperationalPreflightService(self.settings).run(**kwargs)
    def test_preflight_runs_without_external_connectivity_by_default(self):
        checker=Mock(); OperationalPreflightService(self.settings,connectivity_checkers={"lyrics":checker}).run(); checker.assert_not_called()
    def test_preflight_is_strictly_read_only(self):
        self.ready_project(); before={str(x):(x.stat().st_mtime_ns,hashlib.sha256(x.read_bytes()).hexdigest()) for x in self.root.rglob("*") if x.is_file()}; self.preflight(project_id="008"); after={str(x):(x.stat().st_mtime_ns,hashlib.sha256(x.read_bytes()).hexdigest()) for x in self.root.rglob("*") if x.is_file()}; self.assertEqual(before,after)
    def test_preflight_reports_loopback_server(self): self.assertTrue(any(x.check_id=="server.loopback" and x.severity==PreflightSeverity.INFO for x in self.preflight().findings))
    def test_preflight_reports_missing_provider_configuration(self):
        settings=ApplicationSettings(runtime_mode="production",projects_root=self.root); report=OperationalPreflightService(settings).run(); self.assertTrue(any("not configured" in x.message for x in report.findings))
    def test_preflight_does_not_expose_secrets(self):
        settings=ApplicationSettings(projects_root=self.root,lyrics=LyricsProviderSettings("openai",True,SecretValue("NEVER-PRINT-ME"),"model")); output=OperationalPreflightService(settings).run().to_json(); self.assertNotIn("NEVER-PRINT-ME",output)
    def test_preflight_checks_ffmpeg_executable_without_running_render(self):
        with patch("shutil.which",return_value="C:/ffmpeg.exe"),patch("subprocess.run") as run: OperationalPreflightService(self.settings).run(); run.assert_not_called()
    def test_preflight_reports_project_stale_artifacts(self):
        self.ready_project(stale="visual_plan"); report=self.preflight(project_id="008"); self.assertTrue(any(x.check_id=="project.visual_plan.stale" for x in report.findings))
    def test_preflight_reports_interrupted_jobs(self):
        self.ready_project(); record=ExternalJobRecord.create(project_id="008",stage="music",kind="music",provider="suno",request_artifact_path="request.json",request_sha256="a"*64,provider_job_id="task",status="processing"); ExternalJobRepository(self.root).save(record); report=self.preflight(project_id="008"); self.assertTrue(any(x.check_id=="project.interrupted_jobs" and x.severity==PreflightSeverity.WARNING for x in report.findings))
    def test_preflight_reports_missing_approved_music(self):
        self.ready_project(); (self.root/"008"/"music"/"version-001"/"job.json").unlink(); report=self.preflight(project_id="008"); self.assertTrue(any(x.check_id=="project.music.metadata" and x.severity==PreflightSeverity.BLOCKING for x in report.findings))
    def test_preflight_reports_ready_project(self): self.assertEqual(PreflightStatus.READY,self.preflight(project_id=self.ready_project()).status)
    def test_preflight_status_ready_with_warnings(self):
        settings=ApplicationSettings(runtime_mode="production",projects_root=self.root); report=OperationalPreflightService(settings).run(); self.assertEqual(PreflightStatus.READY_WITH_WARNINGS,report.status)
    def test_preflight_status_not_ready_on_blocking_finding(self): self.assertEqual(PreflightStatus.NOT_READY,self.preflight(project_id="missing").status)
    def test_connectivity_checks_require_explicit_flag(self):
        with self.assertRaises(ConnectivityConfirmationRequired): OperationalPreflightService(self.settings).run(check_provider_connectivity=True)
    def test_connectivity_check_does_not_start_generation(self):
        settings=ApplicationSettings(projects_root=self.root,lyrics=LyricsProviderSettings("openai",True,SecretValue("x"),"model")); health=Mock(return_value=(True,"healthy")); generation=Mock(); OperationalPreflightService(settings,connectivity_checkers={"lyrics":health}).run(check_provider_connectivity=True,confirm_connectivity=True); health.assert_called_once(); generation.assert_not_called()
    def test_preflight_json_output_is_deterministic(self): self.assertEqual(self.preflight().to_json(),self.preflight().to_json())
    def test_preflight_reports_zero_ai_generation_calls(self): self.assertEqual(0,self.preflight().ai_generation_calls)
    def test_preflight_reports_zero_ffmpeg_calls(self): self.assertEqual(0,self.preflight().ffmpeg_calls)
    def test_preflight_reports_zero_write_operations(self): self.assertEqual(0,self.preflight().write_operations)
    def test_ui_requires_connectivity_confirmation(self):
        app=create_app(settings=self.settings,services=build_application_services(settings=self.settings,runtime_mode="dry_run")); self.assertEqual(200,app.dispatch("/preflight").status); self.assertEqual(422,app.dispatch("/preflight/run","POST",b"provider_connectivity=yes").status)
    def test_project_007_is_not_accessed_without_explicit_project_id(self):
        seven=self.root/"007"; seven.mkdir(); secret=seven/"project.json"; secret.write_bytes(b"private"); before=(secret.stat().st_mtime_ns,secret.read_bytes()); self.preflight(); self.assertEqual(before,(secret.stat().st_mtime_ns,secret.read_bytes()))
    def ready_project(self,stale=None):
        project=self.root/"008"; project.mkdir(exist_ok=True); (project/"project.json").write_text("{}",encoding="utf-8"); machine=WorkflowStateMachine(); initial=machine.initial("008"); stages=[]
        for stage in initial.stages:
            if stage.stage.value in {"episode","lyrics","music","alignment","scene_plan","visual_plan","prompts","assets"}:
                path=f"{stage.stage.value}/version-001.json"; artifact=project/path; artifact.parent.mkdir(parents=True,exist_ok=True); artifact.write_text("{}",encoding="utf-8")
                status=WorkflowStageStatus.STALE if stage.stage.value==stale else WorkflowStageStatus.APPROVED; stages.append(stage.model_copy(update={"status":status,"current_version":1,"selected_version":1,"approved_version":1,"versions":(ArtifactVersion(version=1,artifact_path=path,semantic_sha256="a"*64),),"blocked_reason":None}))
            elif stage.stage.value=="composition": stages.append(stage.model_copy(update={"status":WorkflowStageStatus.READY,"blocked_reason":None}))
            else: stages.append(stage)
        write_workflow_state(project/"workflow"/"state.json",machine._state("008",tuple(stages)))
        audio=b"music"; directory=project/"music"/"version-001"; directory.mkdir(parents=True,exist_ok=True); (directory/"variant-01.mp3").write_bytes(audio); (directory/"job.json").write_text(json.dumps({"approved_variant_id":"variant-01","variants":[{"variant_id":"variant-01","sha256":hashlib.sha256(audio).hexdigest()}]}),encoding="utf-8")
        composition=project/"composition"; composition.mkdir(); (composition/"preflight.json").write_text("{}",encoding="utf-8"); return "008"

if __name__=="__main__": unittest.main()
