import tempfile,unittest
from pathlib import Path

from app.web_ui.assets import AssetReviewService
from app.web_ui.lyrics import LyricsStageService
from app.web_ui.planning_review import AssetStalenessState,PlanningBuildResult,PlanningReviewService
from app.web_ui.project_creation import AtomicProjectCreationService
from app.web_ui.workflow import WorkflowStageStatus,read_workflow_state
from tests.test_composition_ui import Builder,Sprint18CompositionFixture

class Sprint18EndToEndTests(unittest.TestCase):
    def setUp(self): self.temporary=tempfile.TemporaryDirectory(); self.fx=Sprint18CompositionFixture(self.temporary.name); self.project=self.fx.project
    def tearDown(self): self.temporary.cleanup()
    def test_end_to_end_workflow_requires_approval_at_every_stage(self):
        state=read_workflow_state(self.project/"workflow"/"state.json"); self.assertTrue(all(state.stage(x).status==WorkflowStageStatus.APPROVED for x in ("episode","lyrics","music","alignment","scene_plan","visual_plan","prompts"))); self.assertEqual(WorkflowStageStatus.READY,state.stage("assets").status); self.assertEqual(WorkflowStageStatus.BLOCKED,state.stage("composition").status)
    def test_lyrics_regeneration_never_calls_music_or_video_automatically(self):
        music_calls=self.fx.music.calls; asset_calls=len(self.fx.assets.calls); LyricsStageService(self.project,self.fx.lyrics).generate(feedback="mai simplu"); self.assertEqual(music_calls,self.fx.music.calls); self.assertEqual(asset_calls,len(self.fx.assets.calls))
    def test_prompt_regeneration_affects_only_dependent_asset(self):
        self.fx.approve_assets(); scene2=(self.project/"assets"/"scene-2"/"version-001"/"asset.mp4").read_bytes(); self.fx.builders["prompt_scene"]=Builder(PlanningBuildResult(data={"scene_id":"scene-1","positive_prompt":"unu nou"}))
        PlanningReviewService(self.project,self.fx.builders).regenerate_prompt("scene-1","schimbă"); stale=AssetStalenessState.model_validate_json((self.project/"workflow"/"asset-staleness.json").read_text(encoding="utf-8")); self.assertEqual(("scene-1",),stale.stale_scene_ids); self.assertEqual(scene2,(self.project/"assets"/"scene-2"/"version-001"/"asset.mp4").read_bytes())
    def test_asset_regeneration_affects_only_composition(self):
        self.fx.approve_assets(); music_before=read_workflow_state(self.project/"workflow"/"state.json").stage("music"); scene2=(self.project/"assets"/"scene-2"/"version-001"/"asset.mp4").read_bytes(); AssetReviewService(self.project,self.fx.assets).generate("scene-1",confirmed=True)
        state=read_workflow_state(self.project/"workflow"/"state.json"); self.assertEqual(music_before,state.stage("music")); self.assertEqual(WorkflowStageStatus.STALE,state.stage("composition").status); self.assertEqual(scene2,(self.project/"assets"/"scene-2"/"version-001"/"asset.mp4").read_bytes())
    def test_project_007_is_not_modified(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); seven=root/"007"; seven.mkdir(); target=seven/"project.json"; target.write_bytes(b"protected-007"); before=target.read_bytes()
            AtomicProjectCreationService(root).create({"title":"Nou","description":"Nou.","language":"ro","target_age":"2-5","aspect_ratio":"16:9","main_character_name":"Luca","main_character_description":"Copil.","episode_theme":None,"educational_goal":None,"notes":None})
            self.assertEqual(before,target.read_bytes())

if __name__=="__main__": unittest.main()
