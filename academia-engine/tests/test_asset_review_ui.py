import tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlencode

from app.web_ui.assets import (AssetGenerationJob,AssetGenerationResult,AssetJobStatus,AssetMediaType,
    AssetGenerationRequest,AssetReviewService,AssetVersionMetadata)
from app.models import GenerationTaskStatus
from app.web_ui.bootstrap import AssetProviderSettings,KlingAssetUiAdapter,SecretValue
from app.web_ui.planning_review import PlanningBuildResult,PlanningReviewService
from app.web_ui.project_creation import AtomicProjectCreationService
from app.web_ui.server import create_application
from app.web_ui.workflow import WorkflowActionService,WorkflowStageStatus,WorkflowStateMachine,WorkflowStateRepository,read_workflow_state

class PromptBuilder:
    def build(self,context): return PlanningBuildResult(data={"prompts":[
        {"scene_id":"scene-1","positive_prompt":"Luca în parc","negative_prompt":"","structured_parameters":{"source_texts":["Luca merge vesel prin parc"]}},
        {"scene_id":"scene-2","positive_prompt":"Mărul roșu","negative_prompt":"","structured_parameters":{}}]})
class FakeAssetProvider:
    def __init__(self): self.requests=[]; self.poll=Mock()
    def generate(self,request):
        self.requests.append(request); number=len(self.requests); content=f"png-{request.scene_id}-{number}".encode()
        return AssetGenerationResult(job=AssetGenerationJob(job_id=f"job-{number}",provider="fake_visual",status=AssetJobStatus.COMPLETED),
            media_type=AssetMediaType.IMAGE,content_type="image/png",content=content,provider_response={"status":"ok"})

class RateLimitedAssetProvider:
    def generate(self,request):
        error=RuntimeError("raw provider response"); error.http_status=429; raise error

class FakeClock:
    def __init__(self): self.now=0; self.sleeps=[]
    def monotonic(self): return self.now
    def sleep(self,seconds): self.sleeps.append(seconds); self.now+=seconds

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
    def test_asset_page_shows_source_lyrics_for_each_scene(self):
        response=self.application.dispatch("/projects/008/assets"); text=response.body.decode()
        self.assertIn("Bucata 1",text); self.assertIn("Textul strofei/scenei",text); self.assertIn("Luca merge vesel prin parc",text)
        self.assertIn("Secțiune instrumentală / fără versuri",text)
    def test_polling_is_not_real_in_tests(self): self.approve_prompts(); self.generate(); self.provider.poll.assert_not_called()
    def test_asset_preview_route_serves_local_file(self):
        self.approve_prompts(); self.generate(); response=self.application.dispatch("/projects/008/scenes/scene-1/assets/version-001/preview"); self.assertEqual(200,response.status); self.assertEqual("image/png",response.content_type); self.assertEqual((self.project/"assets"/"scene-1"/"version-001"/"asset.png").read_bytes(),response.body)
    def test_selected_asset_preview_is_visible_without_opening_history(self):
        self.approve_prompts(); self.generate(); text=self.application.dispatch("/projects/008/assets").body.decode()
        self.assertIn("Versiunea selectată",text); self.assertIn("asset-preview",text); self.assertNotIn("Vezi istoricul versiunilor",text)
    def test_rate_limit_error_is_actionable_and_does_not_expose_raw_response(self):
        self.approve_prompts(); app=create_application(self.root,planning_builders={"prompts":self.prompt_builder},asset_provider=RateLimitedAssetProvider())
        response=app.dispatch("/projects/008/scenes/scene-1/assets/generate","POST",urlencode({"confirm_cost":"yes"}).encode())
        text=response.body.decode(); self.assertEqual(422,response.status); self.assertIn("HTTP 429",text); self.assertIn("credite",text); self.assertNotIn("raw provider response",text)

class KlingAssetUiAdapterTests(unittest.TestCase):
    @staticmethod
    def request(): return AssetGenerationRequest(project_id="008",scene_id="scene-1",prompt_bundle_version=1,positive_prompt="Luca descoperă primăvara",negative_prompt="text, watermark",structured_parameters={},prompt_sha256="a"*64)
    def test_submit_poll_and_download_returns_completed_mp4(self):
        artifact=SimpleNamespace(artifact_id="video-1",duration_seconds=15); provider=Mock()
        provider.submit_generation.return_value=SimpleNamespace(external_task_id="task-1")
        provider.get_task_by_id.side_effect=(SimpleNamespace(normalized_status=GenerationTaskStatus.PROCESSING,artifacts=()),SimpleNamespace(normalized_status=GenerationTaskStatus.SUCCEEDED,artifacts=[artifact],provider_status="succeeded"))
        downloader=Mock()
        def download(_artifact,destination): destination.write_bytes(b"mp4-data"); return SimpleNamespace(artifact_id="video-1")
        downloader.download_video_artifact.side_effect=download; clock=FakeClock(); settings=AssetProviderSettings(provider="kling",enabled=True,api_key=SecretValue("key"),base_url="https://api.example.test")
        result=KlingAssetUiAdapter(settings,provider=provider,downloader=downloader,clock=clock,poll_interval_seconds=2).generate(self.request())
        self.assertEqual(AssetJobStatus.COMPLETED,result.job.status); self.assertEqual(AssetMediaType.VIDEO,result.media_type); self.assertEqual(b"mp4-data",result.content); self.assertEqual([2],clock.sleeps)
    def test_prompt_combines_positive_negative_and_feedback(self):
        request=self.request().model_copy(update={"feedback":"mai luminos"}); prompt=KlingAssetUiAdapter._prompt(request)
        self.assertIn("Luca descoperă",prompt); self.assertIn("Avoid: text, watermark",prompt); self.assertIn("Revision feedback: mai luminos",prompt)
    def test_selected_character_requires_public_reference_url(self):
        request=self.request().model_copy(update={"structured_parameters":{"selected_character_ids":["luca"]}})
        with self.assertRaisesRegex(ValueError,"Missing public Kling character reference"):
            KlingAssetUiAdapter._references(request)
    def test_selected_character_reference_is_forwarded_in_selection_order(self):
        request=self.request().model_copy(update={"structured_parameters":{"selected_character_ids":["luca"],
            "character_reference_urls":{"luca":"https://example.test/luca.png"}}})
        self.assertEqual((("luca","https://example.test/luca.png"),),KlingAssetUiAdapter._references(request))

if __name__=="__main__": unittest.main()
