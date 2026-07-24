import tempfile,unittest
from pathlib import Path

from app.web_ui.workflow import *

class WorkflowStateTests(unittest.TestCase):
    def setUp(self): self.temporary=tempfile.TemporaryDirectory(); self.root=Path(self.temporary.name); self.machine=WorkflowStateMachine(); self.state=self.machine.initial("008")
    def tearDown(self): self.temporary.cleanup()
    def test_workflow_status_enum_contains_required_states(self):
        self.assertEqual({"not_started","blocked","ready","running","generated","approved","rejected","stale","failed"},{x.value for x in WorkflowStageStatus})
    def test_workflow_stage_dependencies_are_deterministic(self):
        self.assertEqual(WORKFLOW_DEPENDENCIES,tuple(WorkflowDependency(upstream=a,downstream=b,requirement=c) for a,b,c in DEPENDENCY_PAIRS))
    def test_lyrics_approval_makes_music_ready(self):
        state,_=self.machine.approve(self.state,"episode"); state,_=self.machine.approve(state,"lyrics"); self.assertEqual(WorkflowStageStatus.READY,state.stage("music").status)
    def test_generated_lyrics_do_not_start_music(self):
        state,_=self.machine.approve(self.state,"episode"); state,_=self.machine.set_status(state,"lyrics","generated"); self.assertEqual(WorkflowStageStatus.BLOCKED,state.stage("music").status)
    def test_lyrics_change_marks_downstream_stale(self):
        state=self.machine.change(self.state,"lyrics"); self.assertTrue(all(state.stage(x).status==WorkflowStageStatus.STALE for x in STAGE_ORDER[2:]))
    def test_prompt_change_does_not_invalidate_music(self):
        state=self.machine.change(self.state,"prompts"); self.assertEqual(self.state.stage("music"),state.stage("music")); self.assertEqual(WorkflowStageStatus.STALE,state.stage("assets").status); self.assertEqual(WorkflowStageStatus.STALE,state.stage("composition").status)
    def test_workflow_state_json_is_stable(self):
        a=self.root/"a.json"; b=self.root/"b.json"; write_workflow_state(a,self.state); write_workflow_state(b,self.state); self.assertEqual(a.read_bytes(),b.read_bytes())
    def test_workflow_state_is_reused(self):
        repo=WorkflowStateRepository(self.root); first,reused=repo.resolve("008"); self.assertFalse(reused); second,reused=repo.resolve("008"); self.assertTrue(reused); self.assertEqual(first,second)
    def test_alignment_valid_makes_scene_plan_ready(self):
        state=self.state
        for stage in ("episode","lyrics","music"): state,_=self.machine.approve(state,stage)
        state,_=self.machine.set_status(state,"alignment","generated"); self.assertEqual(WorkflowStageStatus.READY,state.stage("scene_plan").status)
    def test_stage_transition_does_not_execute_next_stage(self):
        state,_=self.machine.approve(self.state,"episode"); self.assertEqual(WorkflowStageStatus.READY,state.stage("lyrics").status); self.assertEqual(0,state.stage("lyrics").current_version)

if __name__=="__main__": unittest.main()
