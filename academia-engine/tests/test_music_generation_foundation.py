import hashlib
import json
import tempfile
import unittest
from collections import deque
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from app.cli.music_engine_task import main as cli_main
from app.models import GenerationTaskStatus
from app.music import (AtomicAudioArtifactDownloader, DurableAudioArtifact, GeneratedAudioArtifact, MusicArtifactCardinalityError,
    MusicEngine, MusicEngineAttemptsExceededError, MusicEngineDownloadError, MusicEngineTaskFailedError,
    MusicEngineContractError, MusicEngineTimeoutError, MusicExternalIdMismatchError, MusicGenerationRequest, MusicGenerationTask,
    MusicGenerationTaskRecord, MusicPollingPolicy, MusicProviderOperationError, MusicProviderTaskIdMismatchError,
    MusicTaskNotFoundError, MusicTaskRegistry, UnsupportedAudioContentTypeError)
from tests.test_song_planning import brief,lyrics,music


NOW=datetime(2026,7,21,10,0,tzinfo=timezone.utc)
POLICY=MusicPollingPolicy(interval_seconds=2,timeout_seconds=10)


def request(): return MusicGenerationRequest(song_id="counting-1-to-5",title="Numărăm",lyrics=lyrics(),music_plan=music())
def audio(content_type="audio/mpeg",url="https://provider.invalid/audio?signed=secret"):
    return GeneratedAudioArtifact(artifact_id="audio-01",download_url=url,content_type=content_type)
def task(status=GenerationTaskStatus.SUBMITTED,artifacts=(),task_id="task-01",external="external-01"):
    return MusicGenerationTask(provider="fake",provider_task_id=task_id,external_correlation_id=external,
                               normalized_status=status,artifacts=tuple(artifacts),created_at=NOW,updated_at=NOW)
def record(status=GenerationTaskStatus.SUBMITTED,artifact=None):
    return MusicGenerationTaskRecord(provider="fake",provider_task_id="task-01",external_correlation_id="external-01",
                                     normalized_status=status,created_at=NOW,updated_at=NOW,artifact=artifact)


class FakeProvider:
    def __init__(self):
        self.submitted=task(); self.queries=deque(); self.submit_calls=0; self.query_calls=0; self.external_calls=0; self.error=None
    def submit_generation(self,value):
        self.submit_calls+=1
        if self.error: raise self.error
        return self.submitted
    def get_task_by_id(self,task_id):
        self.query_calls+=1
        value=self.queries[0] if len(self.queries)==1 else self.queries.popleft()
        if isinstance(value,Exception): raise value
        return value
    def get_task_by_external_id(self,external): self.external_calls+=1; return task(external=external)


class Clock:
    def __init__(self): self.value=0.0; self.sleeps=[]
    def monotonic(self): return self.value
    def sleep(self,value): self.sleeps.append(value); self.value+=value


class MusicFoundationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name); self.registry=MusicTaskRegistry(self.root/"music"/"tasks")
        self.provider=FakeProvider(); self.clock=Clock(); self.reader_calls=0
        def reader(_artifact): self.reader_calls+=1; return b"audio-bytes"
        self.engine=MusicEngine({"fake":self.provider},self.registry,AtomicAudioArtifactDownloader(reader),default_provider="fake",
                                monotonic_clock=self.clock.monotonic,sleeper=self.clock.sleep)
    def tearDown(self): self.temp.cleanup()

    def test_request_reuses_song_contracts_and_rejects_identity_mismatch(self):
        value=request(); self.assertEqual(value.lyrics,lyrics()); self.assertEqual(value.music_plan,music())
        with self.assertRaises(ValidationError): MusicGenerationRequest(song_id="other",title="x",lyrics=lyrics(),music_plan=music())
        with self.assertRaises(ValidationError): MusicPollingPolicy(interval_seconds=0,timeout_seconds=1)

    def test_malformed_provider_task_is_a_contract_error(self):
        self.provider.submitted={"provider":"fake","provider_task_id":"bad id"}
        with self.assertRaises(MusicEngineContractError): self.engine.submit(request())

    def test_submit_exactly_once_creates_atomic_durable_registry_record(self):
        result=self.engine.submit(request())
        self.assertEqual(self.provider.submit_calls,1); self.assertEqual(result,self.registry.load("task-01"))
        manifest=self.root/"music"/"tasks"/"task-01.json"; self.assertTrue(manifest.is_file()); self.assertFalse(manifest.with_suffix(".json.part").exists())

    def test_manifest_excludes_urls_lyrics_music_plan_and_provider_payload(self):
        self.engine.submit(request()); raw=(self.root/"music"/"tasks"/"task-01.json").read_text(encoding="utf-8")
        for forbidden in ("signed=secret","download_url","lyrics","music_plan","learning_objectives","provider_payload","Authorization"):
            self.assertNotIn(forbidden,raw)

    def test_refresh_persists_and_id_mismatch_or_provider_failure_is_safe(self):
        self.registry.create(record()); self.provider.queries=deque([task(GenerationTaskStatus.PROCESSING)])
        self.assertEqual(self.engine.refresh("task-01").normalized_status,GenerationTaskStatus.PROCESSING)
        self.provider.queries=deque([task(task_id="different")])
        with self.assertRaises(MusicProviderTaskIdMismatchError): self.engine.refresh("task-01")
        self.provider.queries=deque([RuntimeError("signed URL Authorization")])
        with self.assertRaises(MusicProviderOperationError) as raised: self.engine.refresh("task-01")
        self.assertNotIn("Authorization",str(raised.exception))

    def test_external_id_lookup_and_mismatch(self):
        result=self.engine.get_task_by_external_id("external-01"); self.assertEqual(result.external_correlation_id,"external-01")
        self.assertEqual(self.provider.external_calls,1)
        self.provider.get_task_by_external_id=lambda value: task(external="wrong")
        with self.assertRaises(MusicExternalIdMismatchError): self.engine.get_task_by_external_id("external-01")

    def test_polling_success_failure_and_no_sleep_after_terminal(self):
        self.registry.create(record()); self.provider.queries=deque([task(),task(GenerationTaskStatus.PROCESSING),task(GenerationTaskStatus.SUCCEEDED)])
        result=self.engine.wait_until_terminal("task-01",POLICY)
        self.assertEqual(result.normalized_status,GenerationTaskStatus.SUCCEEDED); self.assertEqual(self.clock.sleeps,[2,2])
        self.assertEqual(self.provider.query_calls,3)
        self.registry=MusicTaskRegistry(self.root/"failed"); self.registry.create(record()); self.provider=FakeProvider()
        self.provider.queries=deque([task(GenerationTaskStatus.FAILED)]); clock=Clock()
        engine=MusicEngine({"fake":self.provider},self.registry,AtomicAudioArtifactDownloader(lambda _:b"x"),default_provider="fake",
                           monotonic_clock=clock.monotonic,sleeper=clock.sleep)
        self.assertEqual(engine.wait_until_terminal("task-01",POLICY).normalized_status,GenerationTaskStatus.FAILED); self.assertEqual(clock.sleeps,[])

    def test_timeout_and_max_attempts_never_refresh_after_boundary(self):
        for policy,error in ((MusicPollingPolicy(interval_seconds=10,timeout_seconds=3),MusicEngineTimeoutError),
                             (MusicPollingPolicy(interval_seconds=2,timeout_seconds=10,max_attempts=1),MusicEngineAttemptsExceededError)):
            registry=MusicTaskRegistry(self.root/error.__name__); registry.create(record()); provider=FakeProvider()
            provider.queries=deque([task(GenerationTaskStatus.PROCESSING)]); clock=Clock()
            engine=MusicEngine({"fake":provider},registry,AtomicAudioArtifactDownloader(lambda _:b"x"),default_provider="fake",
                               monotonic_clock=clock.monotonic,sleeper=clock.sleep)
            with self.subTest(error=error),self.assertRaises(error): engine.wait_until_terminal("task-01",policy)
            self.assertEqual(provider.query_calls,1)

    def test_polling_provider_failure_stops_without_sleep(self):
        self.registry.create(record()); self.provider.queries=deque([RuntimeError("signed secret")])
        with self.assertRaises(MusicProviderOperationError): self.engine.wait_until_terminal("task-01",POLICY)
        self.assertEqual(self.provider.query_calls,1); self.assertEqual(self.clock.sleeps,[])

    def test_atomic_audio_download_supported_types_hash_and_extension(self):
        for content_type,suffix in (("audio/mpeg",".mp3"),("audio/wav",".wav")):
            destination=self.root/f"song-{suffix[1:]}{suffix}"; downloader=AtomicAudioArtifactDownloader(lambda _:b"abc")
            durable=downloader.download_audio_artifact(audio(content_type),destination)
            self.assertEqual(durable.sha256,hashlib.sha256(b"abc").hexdigest()); self.assertEqual(durable.byte_size,3)
            self.assertEqual(durable.content_type,content_type); self.assertEqual(destination.read_bytes(),b"abc")
            self.assertFalse(destination.with_suffix(suffix+".part").exists())

    def test_download_success_persists_only_after_publication(self):
        self.registry.create(record(GenerationTaskStatus.SUCCEEDED)); self.provider.queries=deque([task(GenerationTaskStatus.SUCCEEDED,[audio()])])
        destination=self.root/"song.mp3"; result=self.engine.download("task-01",destination)
        self.assertEqual(result.artifact.local_path,destination); self.assertEqual(self.reader_calls,1); self.assertTrue(destination.is_file())
        self.assertNotIn("download_url",(self.root/"music"/"tasks"/"task-01.json").read_text(encoding="utf-8"))

    def test_unsupported_empty_and_multiple_artifacts_fail_without_metadata(self):
        scenarios=((task(GenerationTaskStatus.SUCCEEDED,[audio("application/octet-stream")]),UnsupportedAudioContentTypeError),
                   (task(GenerationTaskStatus.SUCCEEDED,[audio(),audio()]),MusicArtifactCardinalityError))
        for index,(response,error) in enumerate(scenarios):
            registry=MusicTaskRegistry(self.root/f"bad-{index}"); registry.create(record(GenerationTaskStatus.SUCCEEDED)); provider=FakeProvider(); provider.queries=deque([response])
            engine=MusicEngine({"fake":provider},registry,AtomicAudioArtifactDownloader(lambda _:b"x"),default_provider="fake")
            with self.subTest(error=error),self.assertRaises(error): engine.download("task-01",self.root/f"bad-{index}.mp3")
            self.assertIsNone(registry.load("task-01").artifact)
        registry=MusicTaskRegistry(self.root/"empty"); registry.create(record(GenerationTaskStatus.SUCCEEDED)); provider=FakeProvider(); provider.queries=deque([task(GenerationTaskStatus.SUCCEEDED,[audio()])])
        engine=MusicEngine({"fake":provider},registry,AtomicAudioArtifactDownloader(lambda _:b""),default_provider="fake")
        with self.assertRaises(MusicEngineDownloadError): engine.download("task-01",self.root/"empty.mp3")
        self.assertIsNone(registry.load("task-01").artifact); self.assertFalse((self.root/"empty.mp3").exists())

    def test_resume_all_states_never_submit(self):
        durable=DurableAudioArtifact(artifact_id="audio-01",local_path=self.root/"existing.mp3",byte_size=3,sha256="a"*64,content_type="audio/mpeg")
        for index,status in enumerate((GenerationTaskStatus.SUBMITTED,GenerationTaskStatus.PROCESSING,GenerationTaskStatus.SUCCEEDED)):
            registry=MusicTaskRegistry(self.root/f"resume-{index}"); registry.create(record(status,durable if index==2 else None)); provider=FakeProvider()
            provider.queries=deque([task(GenerationTaskStatus.SUCCEEDED,[audio()]),task(GenerationTaskStatus.SUCCEEDED,[audio()])])
            engine=MusicEngine({"fake":provider},registry,AtomicAudioArtifactDownloader(lambda _:b"abc"),default_provider="fake")
            result=engine.resume("task-01",self.root/f"resume-{index}.mp3",POLICY)
            self.assertEqual(provider.submit_calls,0); self.assertIsNotNone(result.artifact)
            if index==2: self.assertEqual(provider.query_calls,0)
        registry=MusicTaskRegistry(self.root/"resume-failed"); registry.create(record(GenerationTaskStatus.FAILED)); provider=FakeProvider()
        engine=MusicEngine({"fake":provider},registry,AtomicAudioArtifactDownloader(lambda _:b"x"),default_provider="fake")
        with self.assertRaises(MusicEngineTaskFailedError): engine.resume("task-01",self.root/"failed.mp3",POLICY)
        with self.assertRaises(MusicTaskNotFoundError): engine.resume("missing",self.root/"missing.mp3",POLICY)
        self.assertEqual(provider.submit_calls,0)

    def test_generate_composes_submit_poll_download_once(self):
        self.provider.submitted=task(); succeeded=task(GenerationTaskStatus.SUCCEEDED,[audio()])
        self.provider.queries=deque([succeeded,succeeded]); result=self.engine.generate(request(),self.root/"generated.mp3",POLICY)
        self.assertEqual(self.provider.submit_calls,1); self.assertEqual(self.reader_calls,1); self.assertIsNotNone(result.artifact)

    def test_cli_output_is_sanitized_and_never_prints_signed_url(self):
        artifact=DurableAudioArtifact(artifact_id="audio-01",local_path=Path("song.mp3"),byte_size=3,sha256="a"*64,content_type="audio/mpeg")
        engine=unittest.mock.Mock(); engine.refresh.return_value=record(GenerationTaskStatus.SUCCEEDED,artifact)
        argv=["music_engine_task","--provider","fake","--task-id","task-01","--refresh"]
        with patch("sys.argv",argv),patch("app.cli.music_engine_task.build_music_engine",return_value=engine),patch("builtins.print") as emit:
            self.assertEqual(cli_main(),0)
        output=" ".join(str(call.args[0]) for call in emit.call_args_list)
        for expected in ("Provider task ID: task-01","Status: succeeded","Content type: audio/mpeg"): self.assertIn(expected,output)
        for forbidden in ("signed","Authorization","download_url","lyrics","music_plan","provider payload"): self.assertNotIn(forbidden,output)


if __name__=="__main__": unittest.main()
