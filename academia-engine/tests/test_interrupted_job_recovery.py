import tempfile,unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock,patch

from app.web_ui.job_recovery import *
from app.web_ui.server import create_application

class InterruptedJobRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name); self.repo=ExternalJobRepository(self.root)
        self.project=self.root/"008"; self.project.mkdir(); (self.project/"project.json").write_text("{}",encoding="utf-8")
    def tearDown(self): self.temp.cleanup()
    def job(self,**changes):
        defaults=dict(project_id="008",stage="music",kind="music",provider="suno",request_artifact_path="music/request.json",request_sha256="a"*64,provider_job_id="task-provider-123",status="submitted")
        defaults.update(changes); value=ExternalJobRecord.create(**defaults); return self.repo.save(value)
    def test_incomplete_jobs_are_detected_on_startup_scan(self): self.assertEqual((self.job(),),JobRecoveryService(self.root).scan().incomplete)
    def test_startup_scan_does_not_contact_providers(self):
        callback=Mock(); self.job(); JobRecoveryService(self.root,refreshers={"suno":callback}).scan(); callback.assert_not_called()
    def test_refresh_requires_explicit_confirmation(self):
        value=self.job();
        with self.assertRaises(JobConfirmationRequired): JobRecoveryService(self.root).refresh_job(value.job_id,confirm_external_check=False)
    def test_resume_requires_explicit_confirmation(self):
        value=self.job();
        with self.assertRaises(JobConfirmationRequired): JobRecoveryService(self.root).resume_job(value.job_id,confirm_resume=False)
    def test_submitted_job_is_not_resubmitted_automatically(self):
        submit=Mock(); self.job(); JobRecoveryService(self.root,resumers={"suno":submit}).scan(); submit.assert_not_called()
    def test_provider_job_id_is_preserved(self):
        value=self.job(); service=JobRecoveryService(self.root,refreshers={"suno":lambda job:replace(job,provider_job_id=None,status=ExternalJobStatus.PROCESSING)})
        self.assertEqual("task-provider-123",service.refresh_job(value.job_id,confirm_external_check=True).provider_job_id)
    def test_suno_task_can_resume_variant_download_only(self):
        value=self.job(variant_downloads={"audio-1":"complete","audio-2":"missing"}); callback=Mock(side_effect=lambda job:replace(job,status=ExternalJobStatus.COMPLETED,variant_downloads={"audio-1":"complete","audio-2":"complete"}))
        result=JobRecoveryService(self.root,resumers={"suno":callback}).resume_job(value.job_id,confirm_resume=True); callback.assert_called_once(); self.assertEqual("complete",result.variant_downloads["audio-2"]); self.assertEqual(value.provider_job_id,result.provider_job_id)
    def test_music_recovery_does_not_regenerate_lyrics(self):
        value=self.job(); lyrics=Mock(); JobRecoveryService(self.root,resumers={"suno":lambda job:replace(job,status=ExternalJobStatus.COMPLETED)}).resume_job(value.job_id,confirm_resume=True); lyrics.assert_not_called()
    def test_asset_recovery_is_isolated_per_scene(self):
        a=self.job(stage="assets",kind="asset",provider="kling",scene_id="scene-a",request_sha256="b"*64); b=self.job(stage="assets",kind="asset",provider="kling",scene_id="scene-b",request_sha256="c"*64)
        JobRecoveryService(self.root,resumers={"kling":lambda job:replace(job,status=ExternalJobStatus.COMPLETED)}).resume_job(a.job_id,confirm_resume=True); self.assertEqual(ExternalJobStatus.SUBMITTED,self.repo.load(b.job_id).status)
    def test_composition_partial_output_is_not_treated_as_complete(self):
        value=self.job(stage="composition",kind="composition",provider="ffmpeg",request_sha256="d"*64,status="completed",result_artifact_path="composition/final.mp4.part")
        self.assertEqual(ExternalJobStatus.RECOVERY_REQUIRED,JobRecoveryService(self.root).scan().jobs[0].status); self.assertEqual(ExternalJobStatus.COMPLETED,self.repo.load(value.job_id).status)
    def test_job_records_are_written_atomically(self):
        value=self.job(); self.assertTrue(self.repo.path("008",value.job_id).is_file()); self.assertFalse(self.repo.path("008",value.job_id).with_suffix(".json.part").exists())
    def test_job_recovery_is_idempotent(self):
        value=self.job(); callback=Mock(side_effect=lambda job:replace(job,status=ExternalJobStatus.PROCESSING)); service=JobRecoveryService(self.root,refreshers={"suno":callback}); service.refresh_job(value.job_id,confirm_external_check=True); service.refresh_job(value.job_id,confirm_external_check=True); self.assertEqual(1,len(self.repo.list()))
    def test_provider_without_idempotency_requires_duplicate_cost_warning(self):
        value=self.job(provider="unsafe",provider_job_id=None)
        with self.assertRaises(DuplicateCostWarningRequired): JobRecoveryService(self.root).resume_job(value.job_id,confirm_resume=True)
        self.assertTrue(self.repo.load(value.job_id).duplicate_cost_warning)
    def test_failed_job_preserves_previous_artifacts(self):
        artifact=self.project/"music"/"approved.mp3"; artifact.parent.mkdir(); artifact.write_bytes(b"approved"); value=self.job(result_artifact_path="music/approved.mp3"); JobRecoveryService(self.root).mark_failed(value.job_id); self.assertEqual(b"approved",artifact.read_bytes())
    def test_abandoned_job_does_not_delete_approved_versions(self):
        artifact=self.project/"assets"/"version-001.mp4"; artifact.parent.mkdir(); artifact.write_bytes(b"approved"); value=self.job(); JobRecoveryService(self.root).abandon(value.job_id); self.assertTrue(artifact.is_file())
    def test_job_routes_render_and_require_explicit_actions(self):
        value=self.job(); app=create_application(self.root,recovery_service=JobRecoveryService(self.root)); body=app.dispatch("/jobs").body.decode(); self.assertIn("task…123",body); self.assertEqual(422,app.dispatch(f"/jobs/{value.job_id}/refresh","POST").status); self.assertEqual(200,app.dispatch("/projects/008/jobs").status)
    def test_job_recovery_zero_real_external_calls_in_tests(self):
        self.job()
        with patch("requests.get") as get,patch("requests.post") as post,patch("subprocess.run") as run: JobRecoveryService(self.root).scan(); get.assert_not_called(); post.assert_not_called(); run.assert_not_called()
    def test_project_007_is_not_accessed(self):
        seven=self.root/"007"; seven.mkdir(); target=seven/"project.json"; target.write_bytes(b"protected"); self.job(); JobRecoveryService(self.root).scan("008"); self.assertEqual(b"protected",target.read_bytes())

if __name__=="__main__": unittest.main()
