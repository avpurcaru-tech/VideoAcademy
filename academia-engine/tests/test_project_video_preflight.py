import tempfile
import unittest
from datetime import datetime,timezone
from pathlib import Path

from app.production import (EpisodeSceneResult,EpisodeTransitionPolicy,GenerationRequestReference,
                            GenerationRequestStore,ProductionRecord,ProductionRegistry)
from app.project import ProjectRecord,ProjectRegistry
from app.project.video_preflight import ProjectVideoPreflightError,ProjectVideoPreflightService
from tests.test_episode_production import generation


class ProjectVideoPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name); now=datetime.now(timezone.utc)
        self.projects=ProjectRegistry(self.root/"projects"); self.productions=ProductionRegistry(self.root/"productions")
        self.requests=GenerationRequestStore(self.root/"requests")
        self.projects.create(ProjectRecord(project_id="safe",episode_id="episode",status="failed",video_production_id="safe-video",
            lyrics_path=self.root/"projects/safe/lyrics/lyrics.json",music_directory=self.root/"music",video_directory=self.root/"video",
            final_directory=self.root/"final",created_at=now,updated_at=now))
        refs=tuple(GenerationRequestReference(reference_id=f"safe-scene-{i:04d}") for i in (1,2))
        scenes=tuple(EpisodeSceneResult(scene_id=f"scene-{i:04d}",order=i-1,generation_request_reference=refs[i-1]) for i in (1,2))
        self.productions.create(ProductionRecord(production_id="safe-video",status="failed",provider="kling",scenes=scenes,
            scene_output_directory=self.root/"scenes",final_output_path=self.root/"final.mp4",media_workspace=self.root/"workspace",
            transition_policy=EpisodeTransitionPolicy(kind="cut"),created_at=now,updated_at=now))
        for i,ref in enumerate(refs,1):
            request=generation(f"request-{i}",i)
            request=request.model_copy(update={"video_request":request.video_request.model_copy(update={"duration_seconds":15})})
            self.requests.create(ref,request)

    def tearDown(self): self.temp.cleanup()

    def service(self,environ): return ProjectVideoPreflightService(self.projects,self.productions,self.requests,environ)

    def test_preflight_resolves_every_scene_with_zero_provider_or_http(self):
        project,production,scenes=self.service({"KLING_API_KEY":"configured"}).inspect("safe")
        self.assertEqual(scenes,("scene-0001","scene-0002")); self.assertEqual(production.production_id,project.video_production_id)

    def test_missing_config_and_reference_are_safely_distinguished(self):
        with self.assertRaises(ProjectVideoPreflightError) as caught: self.service({}).inspect("safe")
        self.assertEqual(caught.exception.category,"provider_configuration_invalid")
        (self.root/"requests/safe-scene-0001.json").unlink()
        with self.assertRaises(ProjectVideoPreflightError) as caught: self.service({"KLING_API_KEY":"secret"}).inspect("safe")
        self.assertEqual(caught.exception.category,"request_reference_missing"); self.assertNotIn("secret",str(caught.exception))

    def test_corrupted_record_does_not_expose_contents(self):
        (self.root/"requests/safe-scene-0001.json").write_text('{"prompt":"SECRET"',encoding="utf-8")
        with self.assertRaises(ProjectVideoPreflightError) as caught: self.service({"KLING_API_KEY":"configured"}).inspect("safe")
        self.assertEqual(caught.exception.category,"request_record_corrupted"); self.assertNotIn("SECRET",str(caught.exception))

    def test_uniform_duration_mismatch_reports_only_safe_numbers(self):
        for index in (1,2):
            reference=GenerationRequestReference(reference_id=f"safe-scene-{index:04d}")
            request=self.requests.resolve(reference)
            path=self.root/"requests"/f"safe-scene-{index:04d}.json"
            path.write_text(request.model_copy(update={"video_request":request.video_request.model_copy(
                update={"duration_seconds":10})}).model_dump_json(),encoding="utf-8")
        with self.assertRaises(ProjectVideoPreflightError) as caught:
            self.service({"KLING_API_KEY":"configured"}).inspect("safe")
        self.assertEqual(caught.exception.field_diagnostics,
            (("KLING_DURATION","configured 15, request requires 10"),))

    def test_nonuniform_scene_durations_report_every_scene(self):
        reference=GenerationRequestReference(reference_id="safe-scene-0001")
        request=self.requests.resolve(reference)
        (self.root/"requests/safe-scene-0001.json").write_text(request.model_copy(update={
            "video_request":request.video_request.model_copy(update={"duration_seconds":10})}).model_dump_json(),encoding="utf-8")
        with self.assertRaises(ProjectVideoPreflightError) as caught:
            self.service({"KLING_API_KEY":"configured"}).inspect("safe")
        self.assertEqual(caught.exception.generation_diagnostics,
            ("Scene durations are not uniform: scene-0001=10, scene-0002=15",))


if __name__=="__main__": unittest.main()
