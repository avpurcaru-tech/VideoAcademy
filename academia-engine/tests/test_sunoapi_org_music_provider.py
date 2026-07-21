import tempfile
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import Mock,patch

from app.cli.music_generate import main as cli_main
from app.models import GenerationTaskStatus
from app.music import (AtomicAudioArtifactDownloader,MusicEngine,MusicPollingPolicy,MusicProviderOperationError,
                       MusicTaskRegistry,MusicVariantSelectionRequiredError)
from app.providers.sunoapi_org_music_provider import (RequestsSunoApiOrgTransport,SunoApiOrgContractError,
    SunoApiOrgMusicProvider,SunoCallbackParser,UrllibSunoApiOrgTransport,flatten_lyrics,map_request)
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
    def test_production_requests_transport_matches_working_post_approach(self):
        response=Mock(status_code=200,headers={}); response.json.return_value={"code":200,"msg":"success","data":{"taskId":"task-123"}}
        payload={"customMode":True,"instrumental":False,"model":"V4_5"}
        with patch("app.providers.sunoapi_org_music_provider.requests.post",return_value=response) as post, \
             patch("app.providers.sunoapi_org_music_provider.urllib.request.urlopen") as urllib_open:
            result=RequestsSunoApiOrgTransport("masked-key").request_json("POST","/api/v1/generate",payload)
        self.assertEqual(result["data"]["taskId"],"task-123"); urllib_open.assert_not_called(); post.assert_called_once_with(
            "https://api.sunoapi.org/api/v1/generate",json=payload,
            headers={"Authorization":"Bearer masked-key","Content-Type":"application/json"},timeout=30)

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

    def test_submit_accepts_observed_snake_case_task_id(self):
        provider=SunoApiOrgMusicProvider(FakeTransport(
            {"code":200,"msg":"success","data":{"task_id":"snake-task-123"}}),
            callback_url="https://callback.invalid")
        self.assertEqual(provider.submit_generation(request()).provider_task_id,"snake-task-123")

    def test_submit_extracts_task_id_before_rejecting_observed_alternate_envelopes(self):
        payload={"code":200,"msg":"success","taskId":"root-task"}
        provider=SunoApiOrgMusicProvider(FakeTransport(payload),callback_url="https://callback.invalid")
        with self.assertRaises(SunoApiOrgContractError) as raised: provider.submit_generation(request())
        self.assertEqual(raised.exception.provider_task_id,"root-task")
        shape="\n".join(raised.exception.response_shape)
        self.assertIn("Response root type: object",shape); self.assertNotIn("root-task",shape)

    def test_submit_rejects_string_code_and_missing_task_without_exposing_values(self):
        payload={"code":"200","msg":"lyrics SECRET callback https://secret.invalid","data":{"detail":"SECRET"}}
        provider=SunoApiOrgMusicProvider(FakeTransport(payload),callback_url="https://callback.invalid")
        with self.assertRaises(SunoApiOrgContractError) as raised: provider.submit_generation(request())
        self.assertIsNone(raised.exception.provider_task_id)
        shape="\n".join(raised.exception.response_shape)
        self.assertIn("Field type: code string",shape); self.assertIn("Data field: detail string",shape)
        self.assertNotIn("SECRET",shape); self.assertNotIn("secret.invalid",shape)

    def test_http_success_without_json_and_non_object_json_have_safe_shape(self):
        for parsed,root_type in ((ValueError("raw SECRET"),"invalid-json"),(["SECRET"],"list")):
            response=Mock(status_code=200,headers={})
            if isinstance(parsed,Exception): response.json.side_effect=parsed
            else: response.json.return_value=parsed
            with self.subTest(root_type=root_type),patch(
                    "app.providers.sunoapi_org_music_provider.requests.post",return_value=response):
                with self.assertRaises(SunoApiOrgContractError) as raised:
                    RequestsSunoApiOrgTransport("masked-key").request_json("POST","/api/v1/generate",{})
            shape="\n".join(raised.exception.response_shape)
            self.assertIn(f"Response root type: {root_type}",shape); self.assertNotIn("SECRET",shape)

    def test_engine_persists_recovered_task_id_before_submit_parse_failure(self):
        provider=SunoApiOrgMusicProvider(FakeTransport(
            {"code":200,"msg":{"unexpected":"SECRET"},"data":{"taskId":"paid-task-123"}}),
            callback_url="https://callback.invalid")
        with tempfile.TemporaryDirectory() as directory:
            registry=MusicTaskRegistry(Path(directory)/"tasks")
            engine=MusicEngine({"sunoapi_org":provider},registry,Mock(),default_provider="sunoapi_org")
            with self.assertRaises(MusicProviderOperationError): engine.submit(request())
            record=registry.load("paid-task-123")
        self.assertEqual(record.provider_task_id,"paid-task-123")
        self.assertEqual(record.normalized_status,GenerationTaskStatus.SUBMITTED)

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

    def test_processing_query_allows_placeholder_song_fields_until_success(self):
        payload={"code":200,"msg":"success","data":{"taskId":"task-123","status":"PENDING",
            "response":{"taskId":"task-123","sunoData":[{"id":"","audioUrl":"","duration":None}]}}}
        task=SunoApiOrgMusicProvider(FakeTransport(payload),callback_url="https://callback.invalid").get_task_by_id("task-123")
        self.assertEqual(task.normalized_status,GenerationTaskStatus.SUBMITTED); self.assertEqual(task.artifacts,())

    def test_observed_callback_stages_and_snake_case_artifacts(self):
        for callback_type in ("text","first"):
            payload={"code":200,"msg":"processing","data":{"callbackType":callback_type,
                "task_id":"observed-task","data":[{"id":"song-one","audio_url":"","duration":118}]}}
            task=SunoApiOrgMusicProvider(FakeTransport(payload),callback_url="https://callback.invalid").get_task_by_id("observed-task")
            self.assertEqual(task.normalized_status,GenerationTaskStatus.PROCESSING)
            self.assertEqual(task.artifacts,())
        complete={"code":200,"msg":"All generated successfully.","data":{"callbackType":"complete",
            "task_id":"observed-task","data":[
                {"id":"song-one","audio_url":"https://signed.invalid/one.mp3","duration":118},
                {"id":"song-two","audio_url":"https://signed.invalid/two.mp3","duration":118.76}]}}
        task=SunoApiOrgMusicProvider(FakeTransport(complete),callback_url="https://callback.invalid").get_task_by_id("observed-task")
        self.assertEqual(task.normalized_status,GenerationTaskStatus.SUCCEEDED)
        self.assertEqual([artifact.artifact_id for artifact in task.artifacts],["song-one","song-two"])
        self.assertEqual([artifact.duration_seconds for artifact in task.artifacts],[118.0,118.76])
        self.assertEqual([artifact.download_url for artifact in task.artifacts],
                         ["https://signed.invalid/one.mp3","https://signed.invalid/two.mp3"])

    def test_submit_query_and_callback_share_normalized_contract(self):
        parser=SunoCallbackParser()
        submitted=parser.parse({"code":200,"msg":"created","data":{"task_id":"shared-task"}})
        callback={"code":200,"msg":"complete","data":{"callbackType":"complete","task_id":"shared-task",
            "data":[{"id":"one","audio_url":"https://signed.invalid/one.mp3","duration":10},
                    {"id":"two","audio_url":"https://signed.invalid/two.mp3","duration":10.5}]}}
        parsed_callback=parser.parse(callback)
        queried=SunoApiOrgMusicProvider(FakeTransport(callback),callback_url="https://callback.invalid").get_task_by_id("shared-task")
        self.assertEqual(submitted.normalized_status,GenerationTaskStatus.SUBMITTED)
        self.assertEqual(parsed_callback,queried)
        self.assertEqual([item.duration_seconds for item in queried.artifacts],[10.0,10.5])

    def test_task_id_survives_callback_duration_validation_failure(self):
        payload={"code":200,"msg":"complete","data":{"callbackType":"complete","task_id":"paid-duration-task",
            "data":[{"id":"one","audio_url":"https://signed.invalid/one.mp3","duration":"bad"}]}}
        with self.assertRaises(SunoApiOrgContractError) as raised: SunoCallbackParser().parse(payload)
        self.assertEqual(raised.exception.provider_task_id,"paid-duration-task")

    def test_generate_all_variants_automatically_downloads_real_schema_in_order(self):
        submitted={"code":200,"msg":"created","data":{"task_id":"automatic-task"}}
        text={"code":200,"msg":"text","data":{"callbackType":"text","task_id":"automatic-task","data":[]}}
        complete={"code":200,"msg":"complete","data":{"callbackType":"complete","task_id":"automatic-task",
            "data":[{"id":"one","audio_url":"https://signed.invalid/one.mp3","duration":10},
                    {"id":"two","audio_url":"https://signed.invalid/two.mp3","duration":10.5}]}}
        transport=FakeTransport(submitted,text,complete,complete)
        provider=SunoApiOrgMusicProvider(transport,callback_url="https://callback.invalid")
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); registry=MusicTaskRegistry(root/"tasks")
            engine=MusicEngine({"sunoapi_org":provider},registry,AtomicAudioArtifactDownloader(provider.download_audio_bytes),
                default_provider="sunoapi_org",sleeper=lambda _:None)
            record=engine.generate_all_variants(request(),root/"output",MusicPollingPolicy(interval_seconds=.001,timeout_seconds=5))
            self.assertEqual([item.variant_index for item in record.artifact_set.artifacts],[1,2])
            self.assertEqual([item.artifact_id for item in record.artifact_set.artifacts],["one","two"])
            self.assertTrue((root/"output"/"variant-01.mp3").is_file())
            self.assertTrue((root/"output"/"variant-02.mp3").is_file())
            manifest=(root/"tasks"/"automatic-task.json").read_text(encoding="utf-8")
        for forbidden in ("signed.invalid","audio_url","prompt","callback"): self.assertNotIn(forbidden,manifest)

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
            with self.assertRaises(MusicVariantSelectionRequiredError) as raised:
                engine.generate(request(),root/"song.mp3",MusicPollingPolicy(interval_seconds=1,timeout_seconds=5))
            self.assertEqual((raised.exception.provider_task_id,raised.exception.available_variants),("task-123",2))
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

    def test_cli_submit_shape_diagnostics_are_value_free(self):
        root=Path(__file__).resolve().parents[1]/"examples"/"smoke"; engine=Mock()
        diagnostic=SunoApiOrgContractError("raw SECRET",phase="response_parsing",provider_task_id="paid-task",
            response_shape=("Response root type: object","Field present: data yes","Field type: data object"))
        operation=MusicProviderOperationError("safe"); operation.provider_task_id="paid-task"; operation.__cause__=diagnostic
        engine.generate.side_effect=operation
        argv=["music_generate","--lyrics",str(root/"lyrics-plan.json"),"--music-plan",str(root/"music-plan.json"),
              "--provider","sunoapi_org","--output","song.mp3","--confirm"]
        with patch("sys.argv",argv),patch("app.cli.music_generate.build_music_engine",return_value=engine),patch("builtins.print") as emit:
            self.assertEqual(cli_main(),1)
        output="\n".join(str(call.args[0]) for call in emit.call_args_list)
        self.assertIn("Provider task ID: paid-task",output); self.assertIn("Response root type: object",output)
        for forbidden in ("raw SECRET","lyrics-plan.json","callback","Authorization","masked-key"): self.assertNotIn(forbidden,output)


if __name__=="__main__": unittest.main()
