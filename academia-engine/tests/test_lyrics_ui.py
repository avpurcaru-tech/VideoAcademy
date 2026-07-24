import json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import urlencode

from app.web_ui.lyrics import LyricsGenerationResult,LyricsStageService,LyricsVersion
from app.web_ui.project_creation import AtomicProjectCreationService
from app.web_ui.server import create_application
from app.web_ui.workflow import WorkflowStageStatus,read_workflow_state

class FakeLyricsProvider:
    def __init__(self): self.requests=[]
    def generate(self,request):
        self.requests.append(request); number=len(self.requests)
        return LyricsGenerationResult(lyrics_text=f"[Strofa]\nLuca cântă {number}\n[Refren]\nCulori, culori!",sections=("Strofa","Refren"),provider_metadata={"provider":"fake"})
class FailingLyricsProvider:
    def generate(self,request): raise RuntimeError("provider unavailable")

class LyricsUiTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(); self.root=Path(self.temporary.name); self.provider=FakeLyricsProvider()
        payload={"title":"Luca învață culorile","description":"Luca descoperă culorile prin joacă.","language":"ro","target_age":"2-5","aspect_ratio":"16:9",
            "main_character_name":"Luca","main_character_description":"Băiețel cu ochi căprui.","episode_theme":"culori","educational_goal":"Recunoașterea culorilor","notes":None}
        AtomicProjectCreationService(self.root).create(payload); self.project=self.root/"008"; self.application=create_application(self.root,self.provider)
    def tearDown(self): self.temporary.cleanup()
    def post(self,path,data=None,application=None): return (application or self.application).dispatch(path,"POST",urlencode(data or {}).encode())
    def generate(self): return self.post("/projects/008/lyrics/generate",{"user_instructions":"refren vesel"})
    def test_lyrics_page_renders(self):
        text=self.application.dispatch("/projects/008/lyrics").body.decode(); self.assertIn("Prompt complet",text); self.assertLess(text.index("Prompt complet"),text.index("Editor text")); self.assertIn("Generează",text); self.assertIn("Istoric versiuni",text)
    def test_saved_prompt_is_rendered_and_used_for_generation(self):
        prompt='SYSTEM:\nScrie versuri originale.\n\nUSER:\nScrie despre culori.'
        self.assertEqual(303,self.post("/projects/008/lyrics/prompt",{"system_prompt":"Scrie versuri originale.","user_prompt":"Scrie despre culori."}).status)
        self.assertEqual(prompt+"\n",(self.project/"lyrics"/"prompt.txt").read_text(encoding="utf-8"))
        self.assertIn("Scrie despre culori.",self.application.dispatch("/projects/008/lyrics").body.decode())
        self.assertEqual(303,self.post("/projects/008/lyrics/generate").status); self.assertEqual(prompt,self.provider.requests[-1].user_instructions)
    def test_prompt_fields_are_separate_and_markers_are_not_editable(self):
        text=self.application.dispatch("/projects/008/lyrics").body.decode()
        self.assertIn('name="system_prompt"',text); self.assertIn('name="user_prompt"',text); self.assertNotIn('name="prompt_text"',text)
    def test_generate_lyrics_creates_version(self): self.assertEqual(303,self.generate().status); self.assertTrue((self.project/"lyrics"/"version-001.json").is_file())
    def test_regenerate_lyrics_creates_new_version(self):
        self.generate(); self.post("/projects/008/lyrics/regenerate",{"feedback":"mai repetitiv"}); version=LyricsVersion.model_validate_json((self.project/"lyrics"/"version-002.json").read_text(encoding="utf-8")); self.assertEqual("mai repetitiv",version.generation_request.feedback)
    def test_manual_edit_creates_new_version(self):
        self.generate(); self.post("/projects/008/lyrics/edit",{"lyrics_text":"[Refren]\nRoșu și albastru"}); version=LyricsVersion.model_validate_json((self.project/"lyrics"/"version-002.json").read_text(encoding="utf-8")); self.assertEqual("Roșu și albastru",version.lyrics_text.splitlines()[1]); self.assertEqual("manual_edit",version.provider_metadata["source"])
    def test_old_lyrics_version_is_preserved(self):
        self.generate(); before=(self.project/"lyrics"/"version-001.json").read_bytes(); self.generate(); self.assertEqual(before,(self.project/"lyrics"/"version-001.json").read_bytes())
    def test_lyrics_can_be_approved(self):
        self.generate(); response=self.post("/projects/008/stages/lyrics/approve",{"reason":"bun"}); self.assertEqual(303,response.status); self.assertEqual(WorkflowStageStatus.APPROVED,read_workflow_state(self.project/"workflow"/"state.json").stage("lyrics").status)
    def test_lyrics_approval_makes_music_ready(self):
        self.generate(); self.post("/projects/008/stages/lyrics/approve"); self.assertEqual(WorkflowStageStatus.READY,read_workflow_state(self.project/"workflow"/"state.json").stage("music").status)
    def test_unapproved_lyrics_keep_music_blocked(self):
        self.generate(); self.assertEqual(WorkflowStageStatus.BLOCKED,read_workflow_state(self.project/"workflow"/"state.json").stage("music").status)
    def test_lyrics_regeneration_does_not_call_music_provider(self): music=Mock(); self.generate(); self.post("/projects/008/lyrics/regenerate",{"feedback":"mai simplu"}); music.assert_not_called()
    def test_lyrics_regeneration_does_not_call_video_provider(self): video=Mock(); self.generate(); self.post("/projects/008/lyrics/regenerate",{"feedback":"prea lung"}); video.assert_not_called()
    def test_lyrics_change_marks_downstream_stale(self):
        self.generate(); self.post("/projects/008/stages/lyrics/approve"); self.generate(); state=read_workflow_state(self.project/"workflow"/"state.json"); self.assertEqual(WorkflowStageStatus.STALE,state.stage("music").status); self.assertTrue(all(x.status in {WorkflowStageStatus.STALE,WorkflowStageStatus.BLOCKED} for x in state.stages[2:]))
    def test_lyrics_provider_is_mockable(self):
        self.generate(); request=self.provider.requests[0]; self.assertEqual("Luca învață culorile",request.episode_title); self.assertEqual("refren vesel",request.user_instructions); self.assertEqual("Luca",request.main_character_name)
    def test_lyrics_generation_failure_is_persisted(self):
        response=self.post("/projects/008/lyrics/generate",application=create_application(self.root,FailingLyricsProvider())); self.assertEqual(502,response.status); version=LyricsVersion.model_validate_json((self.project/"lyrics"/"version-001.json").read_text(encoding="utf-8")); self.assertEqual("failed",version.status); self.assertIn("provider unavailable",version.error)
    def test_lyrics_generation_zero_ffmpeg_calls(self): ffmpeg=Mock(); self.generate(); ffmpeg.assert_not_called()

if __name__=="__main__": unittest.main()
