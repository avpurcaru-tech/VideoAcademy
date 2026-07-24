import hashlib,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import urlencode

from app.web_ui.assets import AssetGenerationJob,AssetGenerationResult,AssetJobStatus,AssetMediaType,AssetReviewService
from app.web_ui.composition import CompositionRenderResult,CompositionReviewService
from app.web_ui.lyrics import LyricsGenerationResult,LyricsStageService
from app.web_ui.music import MusicGenerationResult,MusicStageService,MusicVariantResult
from app.web_ui.planning_review import PlanningBuildResult,PlanningReviewService
from app.web_ui.project_creation import AtomicProjectCreationService
from app.web_ui.server import create_application
from app.web_ui.workflow import WorkflowActionService,WorkflowStageStatus,read_workflow_state

class LyricsProvider:
    def __init__(self): self.calls=0
    def generate(self,request): self.calls+=1; return LyricsGenerationResult(lyrics_text="[Refren]\nCulori",sections=("Refren",))
class MusicProvider:
    def __init__(self): self.calls=0
    def generate(self,request): self.calls+=1; return MusicGenerationResult(task_id=f"task-{self.calls}",variants=(MusicVariantResult(audio_id="audio-a",audio_bytes=b"music-a",duration_seconds=10),MusicVariantResult(audio_id="audio-b",audio_bytes=b"music-b",duration_seconds=10)))
class Builder:
    def __init__(self,result): self.result=result; self.calls=0
    def build(self,context): self.calls+=1; return self.result
class AssetProvider:
    def __init__(self): self.calls=[]
    def generate(self,request): self.calls.append(request); data=f"video-{request.scene_id}-{len(self.calls)}".encode(); return AssetGenerationResult(job=AssetGenerationJob(job_id=f"asset-{len(self.calls)}",provider="fake",status=AssetJobStatus.COMPLETED),media_type=AssetMediaType.VIDEO,content_type="video/mp4",content=data,duration_seconds=5)
class Renderer:
    def __init__(self): self.calls=[]
    def render(self,request,destination):
        self.calls.append(request); data=f"final-{len(self.calls)}".encode(); destination.write_bytes(data)
        return CompositionRenderResult(byte_size=len(data),sha256=hashlib.sha256(data).hexdigest(),duration_seconds=request.expected_duration_seconds)

class Sprint18CompositionFixture:
    def __init__(self,root):
        self.root=Path(root); AtomicProjectCreationService(self.root).create({"title":"Culorile","description":"Învățăm.","language":"ro","target_age":"2-5","aspect_ratio":"16:9","main_character_name":"Luca","main_character_description":"Copil vesel.","episode_theme":"culori","educational_goal":"culori","notes":None}); self.project=self.root/"008"
        self.lyrics=LyricsProvider(); LyricsStageService(self.project,self.lyrics).generate(); WorkflowActionService(self.project).execute("008","approve","lyrics")
        self.music=MusicProvider(); music=MusicStageService(self.project,self.music); music.generate(confirmed=True); music.select(1,"variant-02"); music.approve(1)
        self.builders={"alignment":Builder(PlanningBuildResult(data={"coverage":1,"status":"valid"})),
            "scene_plan":Builder(PlanningBuildResult(data={"scenes":[{"scene_id":"scene-1","start":0,"end":5},{"scene_id":"scene-2","start":5,"end":10}]})),
            "visual_plan":Builder(PlanningBuildResult(data={"scenes":[{"scene_id":"scene-1"},{"scene_id":"scene-2"}]})),
            "prompts":Builder(PlanningBuildResult(data={"prompts":[{"scene_id":"scene-1","positive_prompt":"unu"},{"scene_id":"scene-2","positive_prompt":"doi"}]}))}
        planning=PlanningReviewService(self.project,self.builders)
        for stage in ("alignment","scene_plan","visual_plan","prompts"): planning.build(stage); WorkflowActionService(self.project).execute("008","approve",stage)
        self.assets=AssetProvider(); self.renderer=Renderer(); self.application=create_application(self.root,planning_builders=self.builders,asset_provider=self.assets,composition_renderer=self.renderer)
    def approve_assets(self):
        service=AssetReviewService(self.project,self.assets)
        for scene in ("scene-1","scene-2"): service.generate(scene,confirmed=True); service.approve(scene)

class CompositionUiTests(unittest.TestCase):
    def setUp(self): self.temporary=tempfile.TemporaryDirectory(); self.fx=Sprint18CompositionFixture(self.temporary.name); self.project=self.fx.project; self.service=CompositionReviewService(self.project,self.fx.renderer)
    def tearDown(self): self.temporary.cleanup()
    def post(self,path,data=None): return self.fx.application.dispatch(path,"POST",urlencode(data or {}).encode())
    def ready(self): self.fx.approve_assets()
    def test_composition_is_blocked_without_approved_assets(self): result=self.service.preflight(); self.assertFalse(result.ready); self.assertEqual(0,len(self.fx.renderer.calls))
    def test_composition_preflight_is_read_only(self):
        self.ready(); sources={x:x.read_bytes() for x in self.project.rglob("*.mp3")}; response=self.post("/projects/008/composition/preflight"); self.assertEqual(303,response.status); self.assertEqual(sources,{x:x.read_bytes() for x in self.project.rglob("*.mp3")}); self.assertEqual(0,len(self.fx.renderer.calls))
    def test_render_requires_explicit_action(self): self.ready(); self.fx.application.dispatch("/projects/008/composition"); self.assertEqual(0,len(self.fx.renderer.calls))
    def test_render_requires_confirmation(self): self.ready(); self.post("/projects/008/composition/preflight"); self.assertEqual(422,self.post("/projects/008/composition/render").status); self.assertEqual(0,len(self.fx.renderer.calls))
    def test_render_uses_existing_edl(self): self.ready(); expected=self.service.preflight().request.edl; self.post("/projects/008/composition/render",{"confirm_render":"yes"}); self.assertEqual(expected,self.fx.renderer.calls[0].edl)
    def test_render_uses_selected_music_variant(self): self.ready(); self.post("/projects/008/composition/render",{"confirm_render":"yes"}); self.assertEqual("variant-02",self.fx.renderer.calls[0].music_variant_id); self.assertIn("variant-02.mp3",self.fx.renderer.calls[0].music_path)
    def test_render_uses_only_approved_assets(self): self.ready(); self.post("/projects/008/composition/render",{"confirm_render":"yes"}); self.assertEqual({"scene-1":1,"scene-2":1},self.fx.renderer.calls[0].approved_assets)
    def test_rerender_creates_new_version(self): self.ready(); self.post("/projects/008/composition/render",{"confirm_render":"yes"}); self.post("/projects/008/composition/render",{"confirm_render":"yes"}); self.assertTrue((self.project/"composition"/"version-001"/"final.mp4").is_file()); self.assertTrue((self.project/"composition"/"version-002"/"final.mp4").is_file())
    def test_old_render_is_preserved(self): self.ready(); self.post("/projects/008/composition/render",{"confirm_render":"yes"}); before=(self.project/"composition"/"version-001"/"final.mp4").read_bytes(); self.post("/projects/008/composition/render",{"confirm_render":"yes"}); self.assertEqual(before,(self.project/"composition"/"version-001"/"final.mp4").read_bytes())
    def test_final_render_can_be_approved(self): self.ready(); self.post("/projects/008/composition/render",{"confirm_render":"yes"}); self.post("/projects/008/composition/approve",{"version":1}); self.assertEqual(WorkflowStageStatus.APPROVED,read_workflow_state(self.project/"workflow"/"state.json").stage("composition").status)
    def test_dependency_change_marks_composition_stale(self): self.ready(); self.post("/projects/008/composition/render",{"confirm_render":"yes"}); AssetReviewService(self.project,self.fx.assets).generate("scene-1",confirmed=True); self.assertEqual(WorkflowStageStatus.STALE,read_workflow_state(self.project/"workflow"/"state.json").stage("composition").status)
    def test_preflight_makes_zero_ffmpeg_calls(self): self.ready(); self.service.preflight(); self.assertEqual(0,len(self.fx.renderer.calls))
    def test_render_calls_ffmpeg_only_when_explicitly_requested(self): self.ready(); self.service.preflight(); self.assertEqual(0,len(self.fx.renderer.calls)); self.post("/projects/008/composition/render",{"confirm_render":"yes"}); self.assertEqual(1,len(self.fx.renderer.calls))

if __name__=="__main__": unittest.main()
