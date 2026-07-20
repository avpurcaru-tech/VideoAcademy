import tempfile
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import Mock,patch

from app.cli.music_generate import main as cli_main
from app.models import GenerationTaskStatus
from app.music import (AtomicAudioArtifactDownloader,MusicArtifactCardinalityError,MusicEngine,
                       MusicPollingPolicy,MusicTaskRegistry)
from app.providers.sunoapi_org_music_provider import (SunoApiOrgContractError,SunoApiOrgMusicProvider,
    UrllibSunoApiOrgTransport,flatten_lyrics,map_request)
from tests.test_music_generation_foundation import request


class FakeTransport:
    def __init__(self,*responses): self.responses=deque(responses); self.calls=[]
    def request_json(self,method,path,payload=None): self.calls.append((method,path,payload)); return self.responses.popleft()
    def download(self,url): return b"mp3"


def envelope(status="PENDING",songs=None):
    response=None if songs is None else {"taskId":"task-123","sunoData":songs}
    return {"code":200,"msg":"success","data":{"taskId":"task-123","status":status,"response":response}}


SONGS=[{"id":"song-one","audioUrl":"https://signed.invalid/one.mp3"},
       {"id":"song-two","audioUrl":"https://signed.invalid/two.mp3"}]


class SunoApiOrgProviderTests(unittest.TestCase):
    def test_exact_custom_submit_mapping_preserves_lyrics_title_style_and_unicode(self):
        transport=FakeTransport({"code":200,"msg":"success","data":{"taskId":"task-123"}})
        provider=SunoApiOrgMusicProvider(transport,model="V4_5",callback_url="https://callback.invalid/music")
        task=provider.submit_generation(request()); method,path,payload=transport.calls[0]
        self.assertEqual((method,path),("POST","/api/v1/generate"))
        self.assertEqual(set(payload),{"customMode","instrumental","model","callBackUrl","prompt","style","title"})
        self.assertIs(payload["customMode"],True); self.assertIs(payload["instrumental"],False)
        self.assertEqual(payload["model"],"V4_5"); self.assertEqual(payload["title"],request().title)
        self.assertEqual(payload["prompt"],flatten_lyrics(request()))
        self.assertIn(request().lyrics.sections[0].lines[0].text,payload["prompt"])
        self.assertEqual(payload["style"],"pop acustic; mood: jucăuș; instruments: ukulele, xilofon; vocals: voce caldă; tempo: 112 BPM")
        self.assertEqual(task.provider_task_id,"task-123")

    def test_exact_query_parameter_statuses_and_two_mp3_artifacts(self):
        statuses={"PENDING":GenerationTaskStatus.SUBMITTED,"GENERATING":GenerationTaskStatus.PROCESSING,
                  "TEXT_SUCCESS":GenerationTaskStatus.PROCESSING,"FIRST_SUCCESS":GenerationTaskStatus.PROCESSING,
                  "FAILED":GenerationTaskStatus.FAILED,"CREATE_TASK_FAILED":GenerationTaskStatus.FAILED,
                  "GENERATE_AUDIO_FAILED":GenerationTaskStatus.FAILED,"CALLBACK_EXCEPTION":GenerationTaskStatus.FAILED,
                  "SENSITIVE_WORD_ERROR":GenerationTaskStatus.FAILED}
        for source,expected in statuses.items():
            transport=FakeTransport(envelope(source)); task=SunoApiOrgMusicProvider(
                transport,callback_url="https://callback.invalid").get_task_by_id("task-123")
            self.assertEqual(task.normalized_status,expected)
            self.assertEqual(transport.calls[0][:2],("GET","/api/v1/generate/record-info?taskId=task-123"))
        task=SunoApiOrgMusicProvider(FakeTransport(envelope("SUCCESS",SONGS)),callback_url="https://callback.invalid").get_task_by_id("task-123")
        self.assertEqual(task.normalized_status,GenerationTaskStatus.SUCCEEDED); self.assertEqual(len(task.artifacts),2)
        self.assertEqual([a.artifact_id for a in task.artifacts],["song-one","song-two"])
        self.assertTrue(all(a.content_type=="audio/mpeg" for a in task.artifacts))

    def test_unknown_status_and_wrong_success_cardinality_are_rejected(self):
        for payload in (envelope("MYSTERY"),envelope("SUCCESS",SONGS[:1]),envelope("SUCCESS",SONGS+SONGS[:1])):
            with self.subTest(payload=payload),self.assertRaises(SunoApiOrgContractError):
                SunoApiOrgMusicProvider(FakeTransport(payload),callback_url="https://callback.invalid").get_task_by_id("task-123")

    def test_music_engine_exposes_cardinality_conflict_and_persists_no_urls_or_lyrics(self):
        submit={"code":200,"msg":"success","data":{"taskId":"task-123"}}
        transport=FakeTransport(submit,envelope("SUCCESS",SONGS),envelope("SUCCESS",SONGS))
        provider=SunoApiOrgMusicProvider(transport,callback_url="https://callback.invalid")
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); registry=MusicTaskRegistry(root/"tasks")
            engine=MusicEngine({"sunoapi_org":provider},registry,AtomicAudioArtifactDownloader(provider.download_audio_bytes),default_provider="sunoapi_org")
            with self.assertRaises(MusicArtifactCardinalityError):
                engine.generate(request(),root/"song.mp3",MusicPollingPolicy(interval_seconds=1,timeout_seconds=5))
            durable=(root/"tasks"/"task-123.json").read_text(encoding="utf-8")
            for forbidden in ("signed.invalid","audioUrl","lyrics","Numărăm","prompt"):
                self.assertNotIn(forbidden,durable)

    def test_bearer_auth_exact_url_and_submit_is_never_retried(self):
        response=Mock(); response.__enter__=Mock(return_value=response); response.__exit__=Mock(return_value=False)
        response.read.return_value=b'{"code":200,"data":{"taskId":"task-123"}}'
        with patch("urllib.request.urlopen",return_value=response) as opened:
            result=UrllibSunoApiOrgTransport("top-secret").request_json("POST","/api/v1/generate",{"customMode":True})
        self.assertEqual(result["code"],200); self.assertEqual(opened.call_count,1)
        sent=opened.call_args.args[0]; self.assertEqual(sent.full_url,"https://api.sunoapi.org/api/v1/generate")
        self.assertEqual(sent.get_header("Authorization"),"Bearer top-secret")
        with patch("urllib.request.urlopen",side_effect=TimeoutError("secret")) as opened:
            with self.assertRaises(Exception) as raised: UrllibSunoApiOrgTransport("top-secret").request_json("POST","/api/v1/generate",{})
        self.assertEqual(opened.call_count,1); self.assertNotIn("secret",str(raised.exception))

    def test_cli_no_confirm_constructs_nothing_and_uses_gateway_warning(self):
        root=Path(__file__).resolve().parents[1]/"examples"/"smoke"
        argv=["music_generate","--lyrics",str(root/"lyrics-plan.json"),"--music-plan",str(root/"music-plan.json"),
              "--provider","sunoapi_org","--output","song.mp3"]
        with patch("sys.argv",argv),patch("app.cli.music_generate.build_music_engine") as build,patch("builtins.print") as emit:
            self.assertEqual(cli_main(),2)
        build.assert_not_called(); output="\n".join(str(call.args[0]) for call in emit.call_args_list)
        self.assertIn("Real third-party Suno-powered music generation may consume provider credits.",output)
        self.assertNotIn("top-secret",output)

    def test_ambiguous_failure_guidance_is_safe(self):
        root=Path(__file__).resolve().parents[1]/"examples"/"smoke"; engine=Mock(); engine.generate.side_effect=RuntimeError("lyrics SECRET signed-url")
        argv=["music_generate","--lyrics",str(root/"lyrics-plan.json"),"--music-plan",str(root/"music-plan.json"),
              "--provider","sunoapi_org","--output","song.mp3","--confirm"]
        with patch("sys.argv",argv),patch("app.cli.music_generate.build_music_engine",return_value=engine),patch("builtins.print") as emit:
            self.assertEqual(cli_main(),1)
        output="\n".join(str(call.args[0]) for call in emit.call_args_list)
        self.assertIn("provider may have created a paid task",output); self.assertNotIn("SECRET",output)


if __name__=="__main__": unittest.main()
