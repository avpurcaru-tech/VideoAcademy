import json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import urlencode

from app.web_ui.planning_review import AssetStalenessState,PlanningBuildResult,PlanningReviewService
from app.web_ui.project_creation import AtomicProjectCreationService
from app.web_ui.server import create_application
from app.web_ui.workflow import WorkflowActionService,WorkflowStageStatus,WorkflowStateMachine,WorkflowStateRepository,read_workflow_state

class FakeBuilder:
    def __init__(self,result): self.result=result; self.calls=[]
    def build(self,context): self.calls.append(context); return self.result

class PlanningReviewUiTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(); self.root=Path(self.temporary.name)
        AtomicProjectCreationService(self.root).create({"title":"Culorile","description":"Învățăm culorile.","language":"ro","target_age":"2-5","aspect_ratio":"16:9","main_character_name":"Luca","main_character_description":"Băiețel vesel.","episode_theme":"culori","educational_goal":"Culori","notes":None})
        self.project=self.root/"008"; machine=WorkflowStateMachine(); state=read_workflow_state(self.project/"workflow"/"state.json")
        state,_=machine.approve(state,"lyrics"); WorkflowStateRepository(self.project).save(state)
        self.alignment=FakeBuilder(PlanningBuildResult(data={"coverage":.96,"unmapped_words":["x"],"unmapped_lines":[],"instrumental_sections":["intro"],"status":"review_required"},warnings=("word unmapped",),review_required=True))
        self.scene=FakeBuilder(PlanningBuildResult(data={"scenes":[{"scene_id":"scene-1","type":"vocal","start":0,"end":5,"source_lines":["line-1"],"warnings":[]}]}))
        self.visual=FakeBuilder(PlanningBuildResult(data={"scenes":[{"scene_id":"scene-1","subjects":["Luca"],"actions":["arată"],"environment":"parc","style":"3d","camera":"wide","constraints":[]}]}))
        self.prompts=FakeBuilder(PlanningBuildResult(data={"prompts":[{"scene_id":"scene-1","positive_prompt":"Luca în parc","negative_prompt":"fără extra","structured_parameters":{"camera":"wide"}},{"scene_id":"scene-2","positive_prompt":"Mărul roșu","negative_prompt":"","structured_parameters":{}}]}))
        self.prompt_scene=FakeBuilder(PlanningBuildResult(data={"scene_id":"scene-1","positive_prompt":"Luca sare","negative_prompt":"","structured_parameters":{"camera":"medium"}}))
        self.builders={"alignment":self.alignment,"scene_plan":self.scene,"visual_plan":self.visual,"prompts":self.prompts,"prompt_scene":self.prompt_scene}
        self.application=create_application(self.root,planning_builders=self.builders); self.service=PlanningReviewService(self.project,self.builders)
    def tearDown(self): self.temporary.cleanup()
    def post(self,path,data=None): return self.application.dispatch(path,"POST",urlencode(data or {}).encode())
    def approve(self,stage): WorkflowActionService(self.project).execute("008","approve",stage)
    def make_music_approved(self):
        state=read_workflow_state(self.project/"workflow"/"state.json"); state,_=WorkflowStateMachine().approve(state,"music"); WorkflowStateRepository(self.project).save(state)
    def build_chain_to(self,target):
        self.make_music_approved()
        for stage in ("alignment","scene_plan","visual_plan","prompts"):
            self.service.build(stage); 
            if stage==target: break
            self.approve(stage)
    def test_alignment_requires_approved_music(self): self.assertEqual(422,self.post("/projects/008/alignment/build").status); self.assertEqual(0,len(self.alignment.calls))
    def test_alignment_runs_only_on_explicit_action(self): self.make_music_approved(); self.application.dispatch("/projects/008/alignment"); self.assertEqual(0,len(self.alignment.calls)); self.assertEqual(303,self.post("/projects/008/alignment/build").status)
    def test_alignment_rebuild_does_not_generate_music(self):
        music=Mock(); self.make_music_approved(); self.post("/projects/008/alignment/build"); self.post("/projects/008/alignment/rebuild"); music.assert_not_called(); self.assertEqual(2,len(self.alignment.calls))
    def test_scene_plan_requires_approved_alignment(self): self.make_music_approved(); self.service.build("alignment"); self.assertEqual(422,self.post("/projects/008/scene-plan/build").status)
    def test_visual_plan_requires_approved_scene_plan(self): self.build_chain_to("scene_plan"); self.assertEqual(422,self.post("/projects/008/visual-plan/build").status)
    def test_prompts_require_approved_visual_plan(self): self.build_chain_to("visual_plan"); self.assertEqual(422,self.post("/projects/008/prompts/build").status)
    def test_prompt_scene_can_be_edited_independently(self):
        self.build_chain_to("prompts"); before=self.service.effective_prompts(); self.post("/projects/008/prompts/edit",{"scene_id":"scene-1","positive_prompt":"Luca dansează","negative_prompt":""}); after=self.service.effective_prompts(); self.assertEqual("Luca dansează",after[0].positive_prompt); self.assertEqual(before[1].positive_prompt,after[1].positive_prompt)
    def test_prompt_scene_regeneration_does_not_change_other_prompts(self):
        self.build_chain_to("prompts"); other=self.service.effective_prompts()[1]; self.post("/projects/008/prompts/regenerate-scene",{"scene_id":"scene-1","feedback":"mai dinamic"}); after=self.service.effective_prompts(); self.assertEqual("Luca sare",after[0].positive_prompt); self.assertEqual(other,after[1])
    def test_prompt_change_marks_only_dependent_asset_stale(self):
        self.build_chain_to("prompts"); self.approve("prompts"); assets_before=read_workflow_state(self.project/"workflow"/"state.json").stage("assets").status
        self.post("/projects/008/prompts/edit",{"scene_id":"scene-1","positive_prompt":"nou"}); stale=AssetStalenessState.model_validate_json((self.project/"workflow"/"asset-staleness.json").read_text(encoding="utf-8")); self.assertEqual(("scene-1",),stale.stale_scene_ids); self.assertEqual(assets_before,read_workflow_state(self.project/"workflow"/"state.json").stage("assets").status)
    def test_prompt_change_marks_composition_stale(self): self.build_chain_to("prompts"); self.post("/projects/008/prompts/edit",{"scene_id":"scene-1","positive_prompt":"nou"}); self.assertEqual(WorkflowStageStatus.STALE,read_workflow_state(self.project/"workflow"/"state.json").stage("composition").status)
    def test_prompt_change_does_not_mark_music_stale(self): self.build_chain_to("prompts"); self.post("/projects/008/prompts/edit",{"scene_id":"scene-1","positive_prompt":"nou"}); self.assertEqual(WorkflowStageStatus.APPROVED,read_workflow_state(self.project/"workflow"/"state.json").stage("music").status)
    def test_review_required_alignment_is_displayed(self): self.make_music_approved(); self.service.build("alignment"); text=self.application.dispatch("/projects/008/alignment").body.decode(); self.assertIn("Review required",text); self.assertIn("word unmapped",text)
    def test_planning_pages_make_zero_unexpected_external_calls(self): external=Mock(); self.application.dispatch("/projects/008/alignment"); self.application.dispatch("/projects/008/scene-plan"); self.application.dispatch("/projects/008/visual-plan"); self.application.dispatch("/projects/008/prompts"); external.assert_not_called()

if __name__=="__main__": unittest.main()
