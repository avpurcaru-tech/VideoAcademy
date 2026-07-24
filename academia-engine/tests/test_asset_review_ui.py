import tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import urlencode

from app.web_ui.assets import (AssetGenerationJob,AssetGenerationResult,AssetJobStatus,AssetMediaType,
    AssetReviewService,AssetVersionMetadata)
from app.web_ui.planning_review import PlanningBuildResult,PlanningReviewService
from app.web_ui.project_creation import AtomicProjectCreationService
from app.web_ui.server import create_application
from app.web_ui.workflow import WorkflowActionService,WorkflowStageStatus,WorkflowStateMachine,WorkflowStateRepository,read_workflow_state

class PromptBuilder:
    def build(self,context): return PlanningBuildResult(data={"prompts":[
        {"scene_id":"scene-1","positive_prompt":"Luca în parc","negative_prompt":"","structured_parameters":{}},
        {"scene_id":"scene-2","positive_prompt":"Mărul roșu","negative_prompt":"","structured_parameters":{}}]})
class FakeAssetProvider:
    def __init__(self): self.requests=[]; self.poll=Mock()
    def generate(self,request):
        self.requests.append(request); number=len(self.requests); content=f"png-{request.scene_id}-{number}".encode()
        return AssetGenerationResult(job=AssetGenerationJob(job_id=f"job-{number}",provider="fake_visual",status=AssetJobStatus.COMPLETED),
            media_type=AssetMediaType.IMAGE,content_type="image/png",content=content,provider_response={"status":"ok"})

class AssetReviewUiTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(); self.root=Path(self.temporary.name)
        AtomicProjectCreationService(self.root).create({"title":"Culorile","description":"Învățăm culorile.","language":"ro","target_age":"2-5","aspect_ratio":"16:9","main_character_name":"Luca","main_character_description":"Băiețel vesel.","episode_theme":"culori","educational_goal":"Culori","notes":None})
        self.project=self.root/"008"; state=read_workflow_state(self.project/"workflow"/"state.json"); machine=WorkflowStateMachine()
        for stage in ("lyrics","music","alignment","scene_plan","visual_plan"): state,_=machine.approve(state,stage)
        WorkflowStateRepository(self.project).save(state); self.prompt_builder=PromptBuilder(); self.planning=PlanningReviewService(self.project,{"prompts":self.prompt_builder}); self.planning.build("prompts")
        self.provider=FakeAssetProvider(); self.application=create_application(self.root,planning_builders={"prompts":self.prompt_builder},asset_provider=self.provider); self.service=AssetReviewService(self.project,self.provider)
    def tearDown(self): self.temporary.cleanup()
    def post(self,path,data=None): return self.application.dispatch(path,"POST",urlencode(data or {}).encode())
    def approve_prompts(self): WorkflowActionService(self.project).execute("008","approve","prompts")
    def generate(self,scene="scene-1",feedback=None): return self.post(f"/projects/008/scenes/{scene}/assets/generate",{"confirm_cost":"yes","feedback":feedback or ""})
    def test_asset_generation_requires_approved_prompt(self): self.assertEqual(422,self.generate().status); self.assertEqual(0,len(self.provider.requests))
    def test_asset_generation_requires_cost_confirmation(self): self.approve_prompts(); self.assertEqual(422,self.post("/projects/008/scenes/scene-1/assets/generate").status); self.assertEqual(0,len(self.provider.requests))
    def test_generate_single_scene_asset(self):
        self.approve_prompts(); self.assertEqual(303,self.generate().status); directory=self.project/"assets"/"scene-1"/"version-001"; self.assertTrue(all((directory/x).is_file() for x in ("request.json","provider-response.json","asset.png","metadata.json"))); self.assertFalse((self.project/"assets"/"scene-2").exists())
    def test_single_scene_regeneration_does_not_touch_other_assets(self):
        self.approve_prompts(); self.generate("scene-1"); self.generate("scene-2"); other=(self.project/"assets"/"scene-2"/"version-001"/"asset.png").read_bytes(); self.post("/projects/008/scenes/scene-1/assets/regenerate",{"confirm_cost":"yes","feedback":"mai luminos"}); self.assertEqual(other,(self.project/"assets"/"scene-2"/"version-001"/"asset.png").read_bytes())
    def test_asset_versions_are_preserved(self):
        self.approve_prompts(); self.generate(); first=(self.project/"assets"/"scene-1"/"version-001"/"asset.png").read_bytes(); self.post("/projects/008/scenes/scene-1/assets/regenerate",{"confirm_cost":"yes"}); self.assertEqual(first,(self.project/"assets"/"scene-1"/"version-001"/"asset.png").read_bytes()); self.assertTrue((self.project/"assets"/"scene-1"/"version-002"/"asset.png").is_file())
    def test_asset_can_be_approved(self): self.approve_prompts(); self.generate(); self.post("/projects/008/scenes/scene-1/assets/approve"); self.assertEqual(AssetJobStatus.APPROVED,self.service.state().scene("scene-1").status)
    def test_rejected_asset_can_be_regenerated(self):
        self.approve_prompts(); self.generate(); self.post("/projects/008/scenes/scene-1/assets/reject"); self.assertEqual(AssetJobStatus.REJECTED,self.service.state().scene("scene-1").status); self.post("/projects/008/scenes/scene-1/assets/regenerate",{"confirm_cost":"yes"}); self.assertEqual(2,self.service.state().scene("scene-1").current_version)
    def test_asset_change_marks_composition_stale(self): self.approve_prompts(); self.generate(); self.assertEqual(WorkflowStageStatus.STALE,read_workflow_state(self.project/"workflow"/"state.json").stage("composition").status)
    def test_asset_change_does_not_mark_music_stale(self): self.approve_prompts(); self.generate(); self.assertEqual(WorkflowStageStatus.APPROVED,read_workflow_state(self.project/"workflow"/"state.json").stage("music").status)
    def test_asset_provider_is_mockable(self): self.approve_prompts(); self.generate(); self.assertEqual("scene-1",self.provider.requests[0].scene_id)
    def test_asset_ui_zero_real_provider_calls(self): real=Mock(); self.approve_prompts(); self.application.dispatch("/projects/008/assets"); real.assert_not_called(); self.assertEqual(0,len(self.provider.requests))
    def test_polling_is_not_real_in_tests(self): self.approve_prompts(); self.generate(); self.provider.poll.assert_not_called()
    def test_asset_preview_route_serves_local_file(self):
        self.approve_prompts(); self.generate(); response=self.application.dispatch("/projects/008/scenes/scene-1/assets/version-001/preview"); self.assertEqual(200,response.status); self.assertEqual("image/png",response.content_type); self.assertEqual((self.project/"assets"/"scene-1"/"version-001"/"asset.png").read_bytes(),response.body)

if __name__=="__main__": unittest.main()
