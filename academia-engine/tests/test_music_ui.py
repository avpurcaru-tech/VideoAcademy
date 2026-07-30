import tempfile,unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import urlencode

from app.web_ui.lyrics import LyricsGenerationResult,LyricsStageService
from app.models import GenerationTaskStatus
from app.web_ui.music import MusicGenerationRequest,MusicGenerationResult,MusicStageService,MusicVariantResult,SunoApiOrgMusicAdapter,MusicUiError
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

class FakeClock:
    def __init__(self): self.now=0; self.sleeps=[]
    def monotonic(self): return self.now
    def sleep(self,seconds): self.sleeps.append(seconds); self.now+=seconds

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
    def test_music_page_exposes_editable_style_fields(self):
        text=self.application.dispatch("/projects/008/music").body.decode()
        for name in ("musical_style","mood","instrumentation","vocal_style","tempo_bpm"): self.assertIn(f'name="{name}"',text)
        self.assertIn("Romanian children’s song",text); self.assertIn('value="92"',text)
    def test_music_generation_persists_custom_style(self):
        self.approve_lyrics(); response=self.post("/projects/008/music/generate",{"confirm_cost":"yes","musical_style":"playful reggae","mood":"sunny","instrumentation":"marimba, bass, drums","vocal_style":"warm duet","tempo_bpm":"124"})
        self.assertEqual(303,response.status); request=self.music_provider.requests[-1]
        self.assertEqual("playful reggae",request.musical_style); self.assertEqual(("marimba","bass","drums"),request.instrumentation); self.assertEqual(124,request.tempo_bpm)
    def test_music_generation_creates_multiple_variants(self):
        self.approve_lyrics(); self.generate(); directory=self.project/"music"/"version-001"; self.assertTrue(all((directory/f"variant-{x:02d}.json").is_file() and (directory/f"variant-{x:02d}.mp3").is_file() for x in (1,2)))
    def test_audio_variant_can_be_selected(self): self.approve_lyrics(); self.generate(); self.select(1,"variant-02"); self.assertEqual("variant-02",MusicStageService(self.project).versions()[0].selected_variant_id)
    def test_selected_variant_can_be_approved(self):
        self.approve_lyrics(); self.generate(); self.select(); response=self.post("/projects/008/music/approve",{"version":1}); self.assertEqual(303,response.status); self.assertEqual(WorkflowStageStatus.APPROVED,read_workflow_state(self.project/"workflow"/"state.json").stage("music").status)
    def test_generic_workflow_approval_also_persists_approved_music_variant(self):
        self.approve_lyrics(); self.generate(); self.select(); response=self.post("/projects/008/stages/music/approve")
        version=MusicStageService(self.project).versions()[0]
        self.assertEqual(303,response.status); self.assertEqual("variant-01",version.approved_variant_id); self.assertEqual("approved",version.status.value)
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

class SunoUiPollingTests(unittest.TestCase):
    @staticmethod
    def request(): return MusicGenerationRequest(project_id="008",episode_title="Culori",language="ro",target_age="2-5",lyrics_version=1,lyrics_text="[Chorus]\nCulori",lyrics_sha256="a"*64)
    def test_adapter_polls_until_variants_are_ready(self):
        artifact=SimpleNamespace(artifact_id="audio-1",duration_seconds=20,content_type="audio/mpeg",download_url="https://example.test/audio.mp3")
        provider=Mock(); provider.submit_generation.return_value=SimpleNamespace(provider_task_id="task-1")
        provider.get_task_by_id.side_effect=(SimpleNamespace(normalized_status=GenerationTaskStatus.SUBMITTED,artifacts=()),SimpleNamespace(normalized_status=GenerationTaskStatus.PROCESSING,artifacts=()),SimpleNamespace(normalized_status=GenerationTaskStatus.SUCCEEDED,artifacts=(artifact,)))
        provider.download_audio_bytes.return_value=b"mp3"; clock=FakeClock()
        result=SunoApiOrgMusicAdapter(provider,lambda request:request,poll_interval_seconds=2,generation_timeout_seconds=10,clock=clock).generate(self.request())
        self.assertEqual("task-1",result.task_id); self.assertEqual([2,2],clock.sleeps); self.assertEqual(3,provider.get_task_by_id.call_count)
    def test_adapter_stops_on_provider_failure(self):
        provider=Mock(); provider.submit_generation.return_value=SimpleNamespace(provider_task_id="task-1"); provider.get_task_by_id.return_value=SimpleNamespace(normalized_status=GenerationTaskStatus.FAILED,artifacts=())
        with self.assertRaises(MusicUiError): SunoApiOrgMusicAdapter(provider,lambda request:request,clock=FakeClock()).generate(self.request())

if __name__=="__main__": unittest.main()
