import json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import urlencode

from app.web_ui.server import create_application
from app.web_ui.workflow import *

class WorkflowActionTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(); self.root=Path(self.temporary.name); self.project=self.root/"008"; self.project.mkdir(); (self.project/"project.json").write_text("{}",encoding="utf-8")
        self.machine=WorkflowStateMachine(); state,_=self.machine.approve(self.machine.initial("008"),"episode")
        self.repository=WorkflowStateRepository(self.project); self.repository.save(state); self.service=WorkflowActionService(self.project)
    def tearDown(self): self.temporary.cleanup()
    def generated(self,stage="lyrics"):
        return self.service.execute("008","mark_generated",stage,reason="generated")
    def test_generated_stage_can_be_approved(self):
        self.generated(); result=self.service.execute("008","approve","lyrics",reason="acceptat"); self.assertEqual(WorkflowStageStatus.APPROVED,result.to_status); self.assertEqual(1,result.state.stage("lyrics").approved_version)
    def test_not_generated_stage_cannot_be_approved(self):
        with self.assertRaisesRegex(ValueError,"generated"): self.service.execute("008","approve","lyrics")
    def test_approved_stage_can_be_unlocked(self):
        self.generated(); self.service.execute("008","approve","lyrics"); result=self.service.execute("008","unlock","lyrics"); self.assertEqual(WorkflowStageStatus.GENERATED,result.to_status); self.assertEqual(1,result.state.stage("lyrics").approved_version)
    def test_unlock_marks_downstream_stale(self):
        self.generated(); self.service.execute("008","approve","lyrics"); result=self.service.execute("008","unlock","lyrics"); self.assertEqual(WorkflowStageStatus.STALE,result.state.stage("music").status)
    def test_reject_preserves_existing_version(self):
        self.generated(); result=self.service.execute("008","reject","lyrics",reason="revizie"); self.assertEqual(WorkflowStageStatus.REJECTED,result.to_status); self.assertEqual((1,),tuple(x.version for x in result.state.stage("lyrics").versions))
    def test_new_version_does_not_overwrite_old_version(self):
        self.generated(); self.service.execute("008","reject","lyrics"); result=self.generated(); stage=result.state.stage("lyrics"); self.assertEqual((1,2),tuple(x.version for x in stage.versions)); self.assertNotEqual(stage.versions[0].artifact_path,stage.versions[1].artifact_path)
    def test_select_previous_version(self):
        self.generated(); self.generated(); result=self.service.execute("008","select_version","lyrics",version=1); self.assertEqual(1,result.state.stage("lyrics").selected_version); self.assertEqual(2,result.state.stage("lyrics").current_version)
    def test_select_version_updates_downstream_staleness(self):
        self.generated(); self.service.execute("008","approve","lyrics"); self.service.execute("008","mark_generated","music"); self.generated(); result=self.service.execute("008","select_version","lyrics",version=1); self.assertEqual(WorkflowStageStatus.STALE,result.state.stage("music").status)
    def test_approval_unlocks_next_stage_only(self):
        self.generated(); result=self.service.execute("008","approve","lyrics"); self.assertEqual(WorkflowStageStatus.READY,result.state.stage("music").status); self.assertEqual(WorkflowStageStatus.BLOCKED,result.state.stage("alignment").status)
    def test_actions_are_persisted_in_audit_log(self):
        self.generated(); self.service.execute("008","approve","lyrics",reason="bun"); records=[json.loads(x) for x in (self.project/"workflow"/"history.jsonl").read_text(encoding="utf-8").splitlines()]; self.assertEqual(["mark_generated","approve"],[x["action"] for x in records]); self.assertEqual("bun",records[-1]["reason"])
    def test_invalid_stage_action_returns_error(self):
        response=create_application(self.root).dispatch("/projects/008/stages/unknown/approve","POST",b""); self.assertEqual(422,response.status)
    def test_workflow_actions_make_zero_external_calls(self):
        external=Mock(); self.generated(); self.service.execute("008","approve","lyrics"); external.assert_not_called()
    def test_action_endpoint_approves_generated_stage(self):
        self.generated(); response=create_application(self.root).dispatch("/projects/008/stages/lyrics/approve","POST",urlencode({"reason":"ok"}).encode()); self.assertEqual(303,response.status); self.assertEqual(WorkflowStageStatus.APPROVED,read_workflow_state(self.repository.path).stage("lyrics").status)

if __name__=="__main__": unittest.main()
