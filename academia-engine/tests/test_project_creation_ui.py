import json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import urlencode

from app.web_ui.server import create_application
from app.web_ui.workflow import read_workflow_state,WorkflowStageStatus

class ProjectCreationUiTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(); self.root=Path(self.temporary.name); self.root.mkdir(exist_ok=True)
        self.seven=self.root/"007"; self.seven.mkdir(); (self.seven/"project.json").write_bytes(b'{"protected":true}\n'); self.seven_before=(self.seven/"project.json").read_bytes()
        self.application=create_application(self.root); self.valid={"title":"Luca învață culorile","description":"Luca descoperă culorile prin joacă.",
            "language":"ro","target_age":"2-5","aspect_ratio":"16:9","main_character_name":"Luca",
            "main_character_description":"Băiețel brunet cu ochi căprui.","episode_theme":"culori",
            "educational_goal":"Recunoașterea culorilor de bază","notes":"Folosește diacritice românești."}
    def tearDown(self): self.temporary.cleanup()
    def post(self,data=None): return self.application.dispatch("/projects","POST",urlencode(data or self.valid).encode())
    def test_new_project_form_renders(self):
        response=self.application.dispatch("/projects/new"); text=response.body.decode(); self.assertEqual(200,response.status); self.assertIn("Titlu episod",text); self.assertIn("Descriere personaj",text)
    def test_create_project_with_valid_data(self):
        response=self.post(); self.assertEqual(303,response.status); self.assertEqual("/projects/008?created=1",response.headers["Location"])
        self.assertIn("Proiect creat cu succes",self.application.dispatch(response.headers["Location"]).body.decode())
    def test_project_id_is_allocated_without_collision(self):
        self.post(); second=self.post({**self.valid,"title":"Al doilea episod"}); self.assertEqual("/projects/009?created=1",second.headers["Location"]); self.assertTrue((self.root/"008").is_dir()); self.assertTrue((self.root/"009").is_dir())
    def test_project_directories_are_created(self):
        self.post(); self.assertTrue(all((self.root/"008"/name).is_dir() for name in ("lyrics","music","visual","assets","output","workflow")))
    def test_project_manifest_is_persisted(self):
        self.post(); payload=json.loads((self.root/"008"/"project.json").read_text(encoding="utf-8")); self.assertEqual("008",payload["project_id"]); self.assertEqual("16:9",payload["episode"]["aspect_ratio"]); self.assertEqual("Luca",payload["main_character"]["name"])
    def test_project_manifest_preserves_romanian_diacritics(self):
        self.post(); text=(self.root/"008"/"project.json").read_text(encoding="utf-8"); self.assertIn("Băiețel",text); self.assertIn("Recunoașterea",text)
    def test_initial_workflow_state_is_correct(self):
        self.post(); state=read_workflow_state(self.root/"008"/"workflow"/"state.json"); self.assertEqual(WorkflowStageStatus.APPROVED,state.stage("episode").status); self.assertEqual(WorkflowStageStatus.READY,state.stage("lyrics").status); self.assertTrue(all(x.status==WorkflowStageStatus.BLOCKED for x in state.stages[2:]))
    def test_project_creation_does_not_generate_lyrics(self): self.post(); self.assertEqual([],list((self.root/"008"/"lyrics").iterdir()))
    def test_project_creation_does_not_call_suno(self): call=Mock(); self.post(); call.assert_not_called()
    def test_project_creation_does_not_call_ai(self): call=Mock(); self.post(); call.assert_not_called()
    def test_project_creation_rejects_invalid_aspect_ratio(self):
        response=self.post({**self.valid,"aspect_ratio":"4:3"}); self.assertEqual(422,response.status); self.assertFalse((self.root/"008").exists())
    def test_project_creation_rejects_empty_title(self): self.assertEqual(422,self.post({**self.valid,"title":"   "}).status)
    def test_project_creation_prevents_path_traversal(self):
        response=self.post({**self.valid,"title":"../../007"}); self.assertEqual(422,response.status); self.assertEqual(self.seven_before,(self.seven/"project.json").read_bytes())
    def test_project_creation_does_not_modify_project_007(self): self.post(); self.assertEqual(self.seven_before,(self.seven/"project.json").read_bytes())

if __name__=="__main__": unittest.main()
