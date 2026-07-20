import io
import os
import socket
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock,patch

from app.cli.music_generate import main as cli_main
from app.music import (AtomicAudioArtifactDownloader,MusicEngine,MusicProviderOperationError,MusicTaskRegistry)
from app.providers.sunoapi_org_music_provider import (SunoApiOrgApiError,SunoApiOrgAuthenticationError,
    SunoApiOrgConfigurationError,SunoApiOrgContractError,SunoApiOrgMusicProvider,SunoApiOrgNetworkError,
    SunoApiOrgRateLimitError,UrllibSunoApiOrgTransport)
from tests.test_music_generation_foundation import request


class RaisingProvider:
    def __init__(self,error): self.error=error
    def submit_generation(self,value): raise self.error
    def get_task_by_id(self,value): raise AssertionError("query not expected")


class SubmitDiagnosticTests(unittest.TestCase):
    def test_malformed_callback_and_missing_configuration_are_explicit(self):
        with self.assertRaises(SunoApiOrgConfigurationError):
            SunoApiOrgMusicProvider(Mock(),callback_url="https://")
        with patch.dict(os.environ,{"SUNOAPI_ORG_API_KEY":"","SUNOAPI_ORG_CALLBACK_URL":"","SUNOAPI_ORG_MODEL":""},clear=False):
            with self.assertRaises(SunoApiOrgConfigurationError): SunoApiOrgMusicProvider.from_environment(require_explicit_model=True)

    def test_network_before_response_is_distinct_and_sanitized(self):
        failure=urllib.error.URLError(socket.gaierror("API_KEY lyrics callback secret"))
        with patch("urllib.request.urlopen",side_effect=failure):
            with self.assertRaises(SunoApiOrgNetworkError) as raised:
                UrllibSunoApiOrgTransport("top-secret").request_json("POST","/api/v1/generate",{"prompt":"secret lyrics"})
        self.assertEqual(raised.exception.phase,"network_before_response")
        self.assertNotIn("top-secret",str(raised.exception)); self.assertNotIn("lyrics",str(raised.exception))

    def test_http_auth_rate_and_safe_envelope_fields_without_raw_body(self):
        scenarios=((401,SunoApiOrgAuthenticationError),(403,SunoApiOrgAuthenticationError),(429,SunoApiOrgRateLimitError))
        body=b'{"code":429,"msg":"Insufficient credits","requestId":"trace_123","data":null,"raw":"SECRET_BODY lyrics"}'
        for status,expected in scenarios:
            error=urllib.error.HTTPError("https://api.sunoapi.org",status,"raw secret",{"Retry-After":"7"},io.BytesIO(body))
            with self.subTest(status=status),patch("urllib.request.urlopen",side_effect=error):
                with self.assertRaises(expected) as raised: UrllibSunoApiOrgTransport("api-secret").request_json("POST","/api/v1/generate",{})
                value=raised.exception; self.assertEqual(value.http_status,status); self.assertEqual(value.provider_code,429)
                self.assertEqual(value.provider_message,"Insufficient credits"); self.assertEqual(value.provider_request_id,"trace_123")
                self.assertEqual(value.retry_after,"7")
                self.assertNotIn("SECRET_BODY",str(value)); self.assertNotIn("api-secret",str(value))

    def test_provider_nonzero_code_and_malformed_success_without_task_id(self):
        class Transport:
            def __init__(self,payload): self.payload=payload
            def request_json(self,*args): return self.payload
            def download(self,url): raise AssertionError
        provider=SunoApiOrgMusicProvider(Transport({"code":429,"msg":"Insufficient credits","data":None}),callback_url="https://callback.invalid")
        with self.assertRaises(SunoApiOrgApiError) as raised: provider.submit_generation(request())
        self.assertEqual(raised.exception.phase,"provider_application"); self.assertIsNone(raised.exception.provider_task_id)
        provider=SunoApiOrgMusicProvider(Transport({"code":200,"msg":"success","data":{"unexpected":True}}),callback_url="https://callback.invalid")
        with self.assertRaises(SunoApiOrgContractError) as raised: provider.submit_generation(request())
        self.assertEqual(raised.exception.phase,"response_parsing")

    def test_task_id_on_later_submit_failure_is_persisted_immediately(self):
        error=SunoApiOrgApiError("later response failure",phase="provider_application",provider_code=500,provider_task_id="paid-task-123")
        with tempfile.TemporaryDirectory() as directory:
            registry=MusicTaskRegistry(Path(directory)/"tasks"); engine=MusicEngine(
                {"sunoapi_org":RaisingProvider(error)},registry,AtomicAudioArtifactDownloader(lambda artifact:b"x"),default_provider="sunoapi_org")
            with self.assertRaises(MusicProviderOperationError) as raised: engine.submit(request())
            self.assertTrue(registry.exists("paid-task-123")); self.assertEqual(registry.load("paid-task-123").provider,"sunoapi_org")
            self.assertEqual(raised.exception.provider_task_id,"paid-task-123")
            raw=(Path(directory)/"tasks"/"paid-task-123.json").read_text(encoding="utf-8")
            self.assertNotIn("later response failure",raw); self.assertNotIn("prompt",raw)

    def test_preflight_validates_configuration_and_makes_zero_http_calls(self):
        root=Path(__file__).resolve().parents[1]/"examples"/"smoke"
        argv=["music_generate","--lyrics",str(root/"lyrics-plan.json"),"--music-plan",str(root/"music-plan.json"),
              "--provider","sunoapi_org","--output-dir","songs","--download-all","--preflight"]
        environment={"SUNOAPI_ORG_API_KEY":"secret-key","SUNOAPI_ORG_CALLBACK_URL":"https://callback.invalid/music","SUNOAPI_ORG_MODEL":"V4_5"}
        with patch.dict(os.environ,environment,clear=False),patch("sys.argv",argv),patch("app.cli.music_generate.load_application_environment"),patch("urllib.request.urlopen") as http,patch("builtins.print") as emit:
            self.assertEqual(cli_main(),0)
        http.assert_not_called(); output="\n".join(str(c.args[0]) for c in emit.call_args_list)
        self.assertIn("Music generation preflight passed.",output); self.assertNotIn("secret-key",output); self.assertNotIn("callback.invalid",output)

    def test_cli_diagnostic_prints_safe_fields_and_suppresses_semantics(self):
        root=Path(__file__).resolve().parents[1]/"examples"/"smoke"; diagnostic=SunoApiOrgRateLimitError(
            "raw",phase="http_failure",http_status=429,provider_code=429,provider_message="Insufficient credits",
            provider_request_id="trace_123",retry_after="8")
        wrapped=MusicProviderOperationError("wrapped"); wrapped.__cause__=diagnostic; engine=Mock(); engine.generate_all_variants.side_effect=wrapped
        argv=["music_generate","--lyrics",str(root/"lyrics-plan.json"),"--music-plan",str(root/"music-plan.json"),"--provider","sunoapi_org",
              "--output-dir","songs","--download-all","--confirm"]
        with patch("sys.argv",argv),patch("app.cli.music_generate.build_music_engine",return_value=engine),patch("builtins.print") as emit:
            self.assertEqual(cli_main(),1)
        output="\n".join(str(c.args[0]) for c in emit.call_args_list)
        for expected in ("Submit phase: HTTP failure","HTTP status: 429","Provider code: 429","Provider message: Insufficient credits","Provider request ID: trace_123","Retry-After: 8"):
            self.assertIn(expected,output)
        for forbidden in ("Authorization","secret-key","callback.invalid","signed","prompt"):
            self.assertNotIn(forbidden,output)


if __name__=="__main__": unittest.main()
