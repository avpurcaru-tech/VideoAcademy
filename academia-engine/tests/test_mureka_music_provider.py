import tempfile
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import Mock,patch

from app.cli.music_generate import main as cli_main
from app.models import GenerationTaskStatus
from app.music import DurableAudioArtifact,MusicGenerationTaskRecord,MusicProviderRegistry
from app.providers.mureka_music_provider import (MurekaMusicApiError,MurekaMusicAuthenticationError,
    MurekaMusicContractError,MurekaMusicNetworkError,MurekaMusicProvider,MurekaMusicRateLimitError,
    MurekaMusicTimeoutError,UrllibMurekaTransport)
from tests.test_music_generation_foundation import NOW,request


class FakeTransport:
    def __init__(self,*responses): self.responses=deque(responses); self.calls=[]; self.downloads=[]
    def request_json(self,method,path,payload=None): self.calls.append((method,path,payload)); return self.responses.popleft()
    def download(self,url): self.downloads.append(url); return b"wav"


def response(status="preparing",**updates):
    value={"id":"1436211","created_at":1784628000,"model":"mureka-9","status":status,"trace_id":"provider-trace"}
    value.update(updates); return value


class MurekaMusicProviderTests(unittest.TestCase):
    def test_exact_documented_submission_mapping_and_unicode_order(self):
        transport=FakeTransport(response()); provider=MurekaMusicProvider(transport,model="mureka-9")
        task=provider.submit_generation(request()); method,path,payload=transport.calls[0]
        self.assertEqual((method,path),("POST","/v1/song/generate"))
        self.assertEqual(set(payload),{"lyrics","model","n","prompt","stream"})
        self.assertEqual((payload["model"],payload["n"],payload["stream"]),("mureka-9",1,False))
        self.assertTrue(payload["lyrics"].startswith("[Verse]")); self.assertLess(payload["lyrics"].index("[Verse]"),payload["lyrics"].index("[Chorus]"))
        self.assertIn(request().lyrics.sections[0].lines[0].text,payload["lyrics"])
        self.assertEqual(task.provider_task_id,"1436211"); self.assertIsNone(task.external_correlation_id)

    def test_query_status_mapping_and_documented_wav_artifact(self):
        mappings={"preparing":GenerationTaskStatus.SUBMITTED,"queued":GenerationTaskStatus.SUBMITTED,
                  "running":GenerationTaskStatus.PROCESSING,"streaming":GenerationTaskStatus.PROCESSING,
                  "failed":GenerationTaskStatus.FAILED,"timeouted":GenerationTaskStatus.FAILED,"cancelled":GenerationTaskStatus.FAILED}
        for state,expected in mappings.items():
            provider=MurekaMusicProvider(FakeTransport(response(state)))
            self.assertEqual(provider.get_task_by_id("1436211").normalized_status,expected)
        transport=FakeTransport(response("succeeded",choices=[{"index":0,"id":"song-1","url":"https://secret/generic","wav_url":"https://secret/song.wav"}]))
        task=MurekaMusicProvider(transport).get_task_by_id("1436211")
        self.assertEqual(transport.calls[0][:2],("GET","/v1/song/query/1436211"))
        self.assertEqual((task.artifacts[0].artifact_id,task.artifacts[0].content_type),("song-1","audio/wav"))
        self.assertEqual(task.artifacts[0].download_url,"https://secret/song.wav")

    def test_unknown_status_missing_wav_and_cardinality_are_contract_errors(self):
        cases=(response("new_state"),response("succeeded",choices=[]),
               response("succeeded",choices=[{"id":"one","wav_url":"https://x/1.wav"},{"id":"two","wav_url":"https://x/2.wav"}]),
               response("succeeded",choices=[{"id":"one","url":"https://x/file"}]))
        for value in cases:
            with self.subTest(value=value),self.assertRaises(MurekaMusicContractError): MurekaMusicProvider(FakeTransport(value)).get_task_by_id("1436211")

    def test_transport_classifies_rate_limit_without_leaking_body_and_never_retries(self):
        import urllib.error
        error=urllib.error.HTTPError("https://api.mureka.ai",429,"limited",{},None)
        with patch("urllib.request.urlopen",side_effect=error) as opened:
            with self.assertRaises(MurekaMusicRateLimitError) as raised: UrllibMurekaTransport("secret").request_json("GET","/v1/song/query/1")
        self.assertEqual(opened.call_count,1); self.assertNotIn("secret",str(raised.exception))

    def test_transport_classifies_auth_api_timeout_network_and_malformed_json_safely(self):
        import socket,urllib.error
        scenarios=((urllib.error.HTTPError("https://api.mureka.ai",401,"secret body",{},None),MurekaMusicAuthenticationError),
                   (urllib.error.HTTPError("https://api.mureka.ai",500,"secret body",{},None),MurekaMusicApiError),
                   (socket.timeout("secret timeout"),MurekaMusicTimeoutError),
                   (urllib.error.URLError("secret network"),MurekaMusicNetworkError))
        for source,expected in scenarios:
            with self.subTest(expected=expected),patch("urllib.request.urlopen",side_effect=source) as opened:
                with self.assertRaises(expected) as raised: UrllibMurekaTransport("api-secret").request_json("GET","/v1/song/query/1")
                self.assertEqual(opened.call_count,1); self.assertNotIn("secret",str(raised.exception))
        response=Mock(); response.__enter__=Mock(return_value=response); response.__exit__=Mock(return_value=False)
        response.read.return_value=b"not-json SECRET_BODY"
        with patch("urllib.request.urlopen",return_value=response):
            with self.assertRaises(MurekaMusicContractError) as raised: UrllibMurekaTransport("api-secret").request_json("GET","/v1/song/query/1")
        self.assertNotIn("SECRET_BODY",str(raised.exception))

    def test_registry_is_lazy_and_supports_mureka(self):
        factory=Mock(return_value="runtime"); registry=MusicProviderRegistry({"mureka":factory})
        factory.assert_not_called(); self.assertEqual(registry.resolve("mureka"),"runtime"); factory.assert_called_once()

    def test_cli_without_confirm_never_builds_or_submits_and_is_sanitized(self):
        root=Path(__file__).resolve().parents[1]/"examples"/"smoke"
        argv=["music_generate","--lyrics",str(root/"lyrics-plan.json"),"--music-plan",str(root/"music-plan.json"),
              "--provider","mureka","--output","song.wav"]
        with patch("sys.argv",argv),patch("app.cli.music_generate.build_music_engine") as build,patch("builtins.print") as emit:
            self.assertEqual(cli_main(),2)
        build.assert_not_called(); output="\n".join(str(c.args[0]) for c in emit.call_args_list)
        self.assertIn("may consume provider credits",output)
        for forbidden in ("Numărăm","Authorization","MUREKA_API_KEY","wav_url","prompt"):
            self.assertNotIn(forbidden,output)

    def test_confirmed_cli_uses_engine_and_prints_only_durable_result(self):
        root=Path(__file__).resolve().parents[1]/"examples"/"smoke"; artifact=DurableAudioArtifact(
            artifact_id="song-1",local_path=Path("song.wav"),byte_size=3,sha256="a"*64,content_type="audio/wav")
        record=MusicGenerationTaskRecord(provider="mureka",provider_task_id="1436211",normalized_status="succeeded",
            created_at=NOW,updated_at=NOW,artifact=artifact); engine=Mock(); engine.generate.return_value=record
        argv=["music_generate","--lyrics",str(root/"lyrics-plan.json"),"--music-plan",str(root/"music-plan.json"),
              "--provider","mureka","--output","song.wav","--confirm"]
        with patch("sys.argv",argv),patch("app.cli.music_generate.build_music_engine",return_value=engine),patch("builtins.print") as emit:
            self.assertEqual(cli_main(),0)
        engine.generate.assert_called_once(); output="\n".join(str(c.args[0]) for c in emit.call_args_list)
        self.assertIn("Provider task ID: 1436211",output); self.assertNotIn("download_url",output)


if __name__=="__main__": unittest.main()
