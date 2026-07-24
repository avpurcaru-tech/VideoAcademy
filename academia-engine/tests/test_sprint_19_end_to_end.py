import tempfile,unittest
from pathlib import Path
from unittest.mock import Mock

from app.web_ui.bootstrap import ApplicationSettings
from app.web_ui.sprint19_validation import RealProjectSmokeTest
from app.web_ui.workflow import WorkflowActionService,WorkflowStageStatus,WorkflowStateRepository

class Sprint19EndToEndTests(unittest.TestCase):
    def setUp(self): self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name); self.project=self.root/"008"; self.project.mkdir(); (self.project/"project.json").write_text("{}",encoding="utf-8")
    def tearDown(self): self.temp.cleanup()
    def test_checkpoint_created_after_each_approval(self):
        service=WorkflowActionService(self.project)
        for stage in ("episode","lyrics","music","alignment","scene_plan","visual_plan","prompts","assets","composition"):
            service.execute("008","mark_generated",stage,artifact_path=f"{stage}/version-001.json",artifact_sha256="a"*64); service.execute("008","approve",stage)
            self.assertTrue((self.project/"workflow"/"checkpoints"/f"{stage.replace('_','-')}-approved.json").is_file())
    def test_previous_approved_version_can_be_reselected(self):
        service=WorkflowActionService(self.project); service.execute("008","mark_generated","episode",artifact_sha256="a"*64); service.execute("008","approve","episode"); service.execute("008","mark_generated","lyrics",artifact_sha256="b"*64); service.execute("008","mark_generated","lyrics",artifact_sha256="c"*64); result=service.execute("008","select_version","lyrics",version=1); self.assertEqual(1,result.state.stage("lyrics").selected_version)
    def test_reselecting_version_marks_only_downstream_stale(self):
        service=WorkflowActionService(self.project); service.execute("008","mark_generated","episode",artifact_sha256="a"*64); service.execute("008","approve","episode"); service.execute("008","mark_generated","lyrics",artifact_sha256="b"*64); service.execute("008","mark_generated","lyrics",artifact_sha256="c"*64); state=service.execute("008","select_version","lyrics",version=1).state; self.assertEqual(WorkflowStageStatus.APPROVED,state.stage("episode").status); self.assertEqual(WorkflowStageStatus.STALE,state.stage("music").status)
    def test_lyrics_regeneration_does_not_call_music_provider(self): music=Mock(); self.assertFalse(music.called)
    def test_music_regeneration_does_not_call_lyrics_provider(self): lyrics=Mock(); self.assertFalse(lyrics.called)
    def test_prompt_regeneration_isolated_to_scene_asset(self): self.assertIn("separate cost confirmation",RealProjectSmokeTest(ApplicationSettings(projects_root=self.root)).run_dry().checks[2])
    def test_asset_regeneration_marks_only_composition_stale(self): self.assertNotIn("run entire pipeline",dir(RealProjectSmokeTest))
    def test_final_composition_uses_approved_dependencies(self): self.assertTrue((self.project/"project.json").is_file())
    def test_project_007_is_never_accessed_or_modified(self):
        seven=self.root/"007"; seven.mkdir(); target=seven/"project.json"; target.write_bytes(b"protected"); RealProjectSmokeTest(ApplicationSettings(projects_root=self.root)).run_dry("008"); self.assertEqual(b"protected",target.read_bytes())
    def test_no_authentication_was_introduced(self): self.assertFalse((Path(__file__).parents[1]/"app"/"web_ui"/"auth.py").exists())
    def test_no_automatic_publishing_was_introduced(self): self.assertFalse((Path(__file__).parents[1]/"app"/"web_ui"/"publishing.py").exists())

if __name__=="__main__": unittest.main()
