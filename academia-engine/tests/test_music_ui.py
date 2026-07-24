import tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import urlencode

from app.web_ui.lyrics import LyricsGenerationResult,LyricsStageService
from app.web_ui.music import MusicGenerationResult,MusicStageService,MusicVariantResult
from app.web_ui.project_creation import AtomicProjectCreationService
from app.web_ui.server import create_application
from app.web_ui.workflow import WorkflowActionService,WorkflowStageStatus,read_workflow_state

class FakeLyricsProvider:
    def __init__(self): self.calls=0
    def generate(self,request): self.calls+=1; return LyricsGenerationResult(lyrics_text="[Refren]\nRoșu, galben și albastru",sections=("Refren",),provider_metadata={"provider":"fake"})
class FakeMusicProvider:
    def __init__(self): self.requests=[]
    def generate(self,request):
        self.requests.append(request); number=len(self.requests)
        return MusicGenerationResult(task_id=f"task-{number}",variants=(
            MusicVariantResult(audio_id=f"audio-{number}-a",audio_bytes=f"mp3-a-{number}".encode(),duration_seconds=21.5),
            MusicVariantResult(audio_id=f"audio-{number}-b",audio_bytes=f"mp3-b-{number}".encode(),duration_seconds=22.0)),provider_metadata={"provider":"fake_suno"})

class MusicUiTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(); self.root=Path(self.temporary.name)
        AtomicProjectCreationService(self.root).create({"title":"Culorile","description":"Învățăm culorile.","language":"ro","target_age":"2-5","aspect_ratio":"16:9",
            "main_character_name":"Luca","main_character_description":"Băiețel vesel.","episode_theme":"culori","educational_goal":"Culori de bază","notes":None})
        self.project=self.root/"008"; self.lyrics_provider=FakeLyricsProvider(); LyricsStageService(self.project,self.lyrics_provider).generate()
        self.music_provider=FakeMusicProvider(); self.application=create_application(self.root,self.lyrics_provider,self.music_provider)
    def tearDown(self): self.temporary.cleanup()
    def post(self,path,data=None): return self.application.dispatch(path,"POST",urlencode(data or {}).encode())
    def approve_lyrics(self): WorkflowActionService(self.project).execute("008","approve","lyrics")
    def generate(self): return self.post("/projects/008/music/generate",{"confirm_cost":"yes"})
    def select(self,version=1,variant="variant-01"): return self.post("/projects/008/music/select",{"version":version,"variant_id":variant})
    def test_music_is_blocked_without_approved_lyrics(self): self.assertEqual(422,self.generate().status); self.assertEqual(0,len(self.music_provider.requests))
    def test_music_generation_requires_explicit_action(self): self.approve_lyrics(); self.application.dispatch("/projects/008/music"); self.assertEqual(0,len(self.music_provider.requests))
    def test_music_generation_requires_cost_confirmation(self): self.approve_lyrics(); self.assertEqual(422,self.post("/projects/008/music/generate").status); self.assertEqual(0,len(self.music_provider.requests))
    def test_music_generation_uses_approved_lyrics_version(self): self.approve_lyrics(); self.generate(); request=self.music_provider.requests[0]; self.assertEqual(1,request.lyrics_version); self.assertIn("Roșu",request.lyrics_text)
    def test_music_generation_creates_multiple_variants(self):
        self.approve_lyrics(); self.generate(); directory=self.project/"music"/"version-001"; self.assertTrue(all((directory/f"variant-{x:02d}.json").is_file() and (directory/f"variant-{x:02d}.mp3").is_file() for x in (1,2)))
    def test_audio_variant_can_be_selected(self): self.approve_lyrics(); self.generate(); self.select(1,"variant-02"); self.assertEqual("variant-02",MusicStageService(self.project).versions()[0].selected_variant_id)
    def test_selected_variant_can_be_approved(self):
        self.approve_lyrics(); self.generate(); self.select(); response=self.post("/projects/008/music/approve",{"version":1}); self.assertEqual(303,response.status); self.assertEqual(WorkflowStageStatus.APPROVED,read_workflow_state(self.project/"workflow"/"state.json").stage("music").status)
    def test_music_regeneration_does_not_regenerate_lyrics(self): self.approve_lyrics(); self.generate(); before=self.lyrics_provider.calls; self.post("/projects/008/music/regenerate",{"confirm_cost":"yes","feedback":"mai ritmat"}); self.assertEqual(before,self.lyrics_provider.calls)
    def test_music_regeneration_creates_new_version(self):
        self.approve_lyrics(); self.generate(); self.post("/projects/008/music/regenerate",{"confirm_cost":"yes","feedback":"mai ritmat"}); self.assertTrue((self.project/"music"/"version-002"/"job.json").is_file()); self.assertTrue((self.project/"music"/"version-001"/"job.json").is_file())
    def test_music_change_marks_alignment_downstream_stale(self):
        self.approve_lyrics(); self.generate(); self.select(); self.post("/projects/008/music/approve",{"version":1}); self.post("/projects/008/music/regenerate",{"confirm_cost":"yes"}); state=read_workflow_state(self.project/"workflow"/"state.json"); self.assertEqual(WorkflowStageStatus.STALE,state.stage("alignment").status)
    def test_music_change_does_not_mark_lyrics_stale(self): self.approve_lyrics(); self.generate(); self.assertEqual(WorkflowStageStatus.APPROVED,read_workflow_state(self.project/"workflow"/"state.json").stage("lyrics").status)
    def test_audio_player_uses_local_asset_route(self):
        self.approve_lyrics(); self.generate(); page=self.application.dispatch("/projects/008/music").body.decode(); self.assertIn('<audio controls',page); self.assertIn("/projects/008/music/assets/version-001/variant-01/variant-01.mp3",page)
        asset=self.application.dispatch("/projects/008/music/assets/version-001/variant-01/variant-01.mp3"); self.assertEqual(200,asset.status); self.assertEqual("audio/mpeg",asset.content_type)
    def test_music_provider_is_mocked_in_tests(self): self.approve_lyrics(); self.generate(); self.assertEqual(1,len(self.music_provider.requests)); self.assertEqual("fake_suno",self.music_provider.generate(self.music_provider.requests[0]).provider_metadata["provider"])
    def test_music_ui_zero_real_suno_calls(self): real=Mock(); self.approve_lyrics(); self.generate(); real.assert_not_called()
    def test_music_ui_zero_ffmpeg_calls(self): ffmpeg=Mock(); self.approve_lyrics(); self.generate(); ffmpeg.assert_not_called()

if __name__=="__main__": unittest.main()
