import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch

from app.web_ui.bootstrap import ApplicationSettings
from app.web_ui.job_recovery import ExternalJobRecord,ExternalJobRepository,ExternalJobStatus,JobRecoveryService
from app.web_ui.server import create_application
from app.web_ui.sprint19_validation import *
from app.web_ui.workflow import WorkflowActionService,WorkflowStateRepository

class FirstRealProjectValidationTests(unittest.TestCase):
    def setUp(self): self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name); self.settings=ApplicationSettings(projects_root=self.root)
    def tearDown(self): self.temp.cleanup()
    def test_real_project_dry_run_performs_zero_paid_calls(self):
        report=RealProjectSmokeTest(self.settings).run_dry(); self.assertEqual((0,0,0,0),(report.external_http_calls,report.ai_generation_calls,report.ffmpeg_calls,report.paid_calls))
    def test_real_project_requires_separate_confirmation_for_each_external_stage(self):
        smoke=RealProjectSmokeTest(self.settings)
        for provider,action in (("openai","lyrics"),("suno","music"),("kling","assets")):
            with self.assertRaises(CostConfirmationRequired): smoke.confirm_stage(CostConfirmation(provider,action,"008",False,None))
    def test_no_single_action_runs_entire_pipeline(self): self.assertFalse(hasattr(RealProjectSmokeTest,"run_all")); self.assertFalse(hasattr(RealProjectSmokeTest,"run_paid_pipeline"))
    def test_first_project_can_be_created_through_ui(self):
        body=("title=Test&description=Descriere&language=ro&target_age=2-5&aspect_ratio=16%3A9&main_character_name=Luca&main_character_description=Copil").encode(); response=create_application(self.root).dispatch("/projects","POST",body); self.assertEqual(303,response.status); self.assertTrue(any(self.root.glob("*/project.json")))
    def test_interrupted_music_job_can_resume_without_new_generation(self):
        project=self.root/"008"; project.mkdir(); value=ExternalJobRecord.create(project_id="008",stage="music",kind="music",provider="suno",provider_job_id="task-1",request_sha256="a"*64,request_artifact_path="request.json",status="processing",variant_downloads={"a":"missing"}); ExternalJobRepository(self.root).save(value); generation=Mock(); result=JobRecoveryService(self.root,resumers={"suno":lambda job:job.__class__(**{**job.__dict__,"status":ExternalJobStatus.COMPLETED,"variant_downloads":{"a":"complete"}})}).resume_job(value.job_id,confirm_resume=True); generation.assert_not_called(); self.assertEqual("complete",result.variant_downloads["a"])
    def test_interrupted_asset_job_can_resume_per_scene(self):
        project=self.root/"008"; project.mkdir(); repo=ExternalJobRepository(self.root); a=ExternalJobRecord.create(project_id="008",stage="assets",kind="asset",provider="kling",provider_job_id="a",request_sha256="a"*64,request_artifact_path="a.json",status="processing",scene_id="scene-a"); b=ExternalJobRecord.create(project_id="008",stage="assets",kind="asset",provider="kling",provider_job_id="b",request_sha256="b"*64,request_artifact_path="b.json",status="processing",scene_id="scene-b"); repo.save(a); repo.save(b); JobRecoveryService(self.root,resumers={"kling":lambda job:job.__class__(**{**job.__dict__,"status":ExternalJobStatus.COMPLETED})}).resume_job(a.job_id,confirm_resume=True); self.assertEqual(ExternalJobStatus.PROCESSING,repo.load(b.job_id).status)
    def test_operational_preflight_runs_before_paid_action(self):
        smoke=RealProjectSmokeTest(self.settings); report=smoke.run_dry(); self.assertIn(report.operational_status,{"ready","ready_with_warnings","not_ready"})
    def test_checkpoint_contains_no_secrets(self):
        project=self._project(); action=WorkflowActionService(project); action.execute("008","mark_generated","episode",artifact_path="project.json",artifact_sha256="a"*64); action.execute("008","approve","episode"); text=(project/"workflow"/"checkpoints"/"episode-approved.json").read_text(); self.assertNotIn("api_key",text); self.assertNotIn("secret",text.casefold())
    def test_sprint_19_validation_is_read_only_in_dry_run(self):
        seven=self.root/"007"; seven.mkdir(); file=seven/"project.json"; file.write_bytes(b"protected"); before=(file.stat().st_mtime_ns,file.read_bytes()); RealProjectSmokeTest(self.settings).run_dry(); self.assertEqual(before,(file.stat().st_mtime_ns,file.read_bytes()))
    def test_sprint_19_validation_reports_zero_real_calls_in_tests(self):
        with patch("requests.get") as get,patch("requests.post") as post,patch("subprocess.run") as run: RealProjectSmokeTest(self.settings).run_dry(); get.assert_not_called(); post.assert_not_called(); run.assert_not_called()
    def _project(self):
        project=self.root/"008"; project.mkdir(); (project/"project.json").write_text("{}",encoding="utf-8"); return project

if __name__=="__main__": unittest.main()
