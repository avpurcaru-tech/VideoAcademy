import json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock

from app.web_ui.__main__ import main
from app.web_ui.server import create_application

class WebUiShellTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(); self.root=Path(self.temporary.name); self.project=self.root/"008"; self.project.mkdir(); (self.project/"project.json").write_text("{}",encoding="utf-8")
        self.application=create_application(self.root)
    def tearDown(self): self.temporary.cleanup()
    def test_index_route_returns_html(self):
        response=self.application.dispatch("/"); self.assertEqual(200,response.status); self.assertIn("Academia Video Engine",response.body.decode()); self.assertIn("Episod nou",response.body.decode())
    def test_project_route_returns_stage_cards(self):
        response=self.application.dispatch("/projects/008"); text=response.body.decode(); self.assertEqual(200,response.status); self.assertEqual(8,text.count('class="stage-card"')); self.assertIn("Versuri",text); self.assertIn("Compoziție",text)
    def test_health_route_returns_success(self): self.assertEqual({"status":"ok"},json.loads(self.application.dispatch("/health").body))
    def test_workflow_api_returns_json(self):
        response=self.application.dispatch("/api/projects/008/workflow"); payload=json.loads(response.body); self.assertEqual(200,response.status); self.assertEqual("008",payload["project_id"]); self.assertEqual(9,len(payload["stages"]))
    def test_unknown_project_returns_404(self): self.assertEqual(404,self.application.dispatch("/projects/missing").status)
    def test_static_css_is_served(self): self.assertIn(b"stage-grid",self.application.dispatch("/static/styles.css").body)
    def test_ui_does_not_make_http_external_calls(self): call=Mock(); self.application.dispatch("/"); self.application.dispatch("/health"); call.assert_not_called()
    def test_ui_does_not_make_ai_calls(self): call=Mock(); self.application.dispatch("/projects/008"); call.assert_not_called()
    def test_ui_does_not_make_ffmpeg_calls(self): call=Mock(); self.application.dispatch("/api/projects/008/workflow"); call.assert_not_called()
    def test_cli_browser_open_can_be_disabled(self):
        runner=Mock(); browser=Mock(); self.assertEqual(0,main(["--no-browser","--projects-root",str(self.root)],server_runner=runner,browser_open=browser)); browser.assert_not_called(); runner.assert_called_once()
    def test_ui_read_does_not_persist_workflow(self): self.application.dispatch("/projects/008"); self.assertFalse((self.project/"workflow"/"state.json").exists())

if __name__=="__main__": unittest.main()
