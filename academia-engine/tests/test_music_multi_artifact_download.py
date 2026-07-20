import tempfile
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import Mock,patch

from app.cli.music_engine_task import main as task_cli
from app.cli.music_generate import main as generate_cli
from app.models import GenerationTaskStatus
from app.music import (AtomicAudioArtifactDownloader,DurableAudioArtifactSet,MusicEngine,MusicEngineDownloadError,
                       MusicGenerationTaskRecord,MusicPollingPolicy,MusicTaskRegistry)
from tests.test_music_generation_foundation import FakeProvider,NOW,record
from tests.test_music_variant_selection import variants_task


class MusicMultiArtifactDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name); self.registry=MusicTaskRegistry(self.root/"tasks")
        self.registry.create(record(GenerationTaskStatus.SUCCEEDED)); self.provider=FakeProvider(); self.calls=[]
    def tearDown(self): self.temp.cleanup()

    def engine(self,reader):
        return MusicEngine({"fake":self.provider},self.registry,AtomicAudioArtifactDownloader(reader),default_provider="fake")

    def test_downloads_all_in_provider_order_with_deterministic_atomic_files(self):
        self.provider.queries=deque([variants_task()])
        def reader(artifact): self.calls.append(artifact.artifact_id); return artifact.artifact_id.encode()
        result=self.engine(reader).download_all_variants("task-01",self.root/"songs")
        self.assertEqual(self.provider.query_calls,1); self.assertEqual(self.calls,["variant-one","variant-two"])
        artifact_set=result.artifact_set; self.assertIsInstance(artifact_set,DurableAudioArtifactSet); self.assertTrue(artifact_set.complete)
        self.assertEqual([a.variant_index for a in artifact_set.artifacts],[1,2])
        self.assertEqual([a.local_path.name for a in artifact_set.artifacts],["variant-01.mp3","variant-02.mp3"])
        self.assertEqual([a.artifact_id for a in artifact_set.artifacts],["variant-one","variant-two"])
        self.assertFalse(any((self.root/"songs").glob("*.part")))
        raw=(self.root/"tasks"/"task-01.json").read_text(encoding="utf-8")
        self.assertNotIn("signed.invalid",raw); self.assertNotIn("download_url",raw)

    def test_partial_failure_is_durable_and_resume_downloads_only_missing_variant(self):
        self.provider.queries=deque([variants_task(),variants_task()]); attempts=[]
        def reader(artifact):
            attempts.append(artifact.artifact_id)
            if artifact.artifact_id=="variant-two" and attempts.count("variant-two")==1: raise RuntimeError("temporary signed URL failure")
            return b"audio"
        engine=self.engine(reader)
        with self.assertRaises(MusicEngineDownloadError): engine.download_all_variants("task-01",self.root/"partial")
        partial=self.registry.load("task-01").artifact_set
        self.assertFalse(partial.complete); self.assertEqual([a.artifact_id for a in partial.artifacts],["variant-one"])
        self.assertTrue((self.root/"partial"/"variant-01.mp3").is_file()); self.assertFalse((self.root/"partial"/"variant-02.mp3").exists())
        result=engine.download_all_variants("task-01",self.root/"partial")
        self.assertTrue(result.artifact_set.complete); self.assertEqual(attempts,["variant-one","variant-two","variant-two"])

    def test_complete_operation_returns_without_provider_or_download(self):
        self.provider.queries=deque([variants_task()]); engine=self.engine(lambda artifact:b"audio")
        first=engine.download_all_variants("task-01",self.root/"complete"); calls=self.provider.query_calls
        second=engine.download_all_variants("task-01",self.root/"ignored")
        self.assertEqual(second,first); self.assertEqual(self.provider.query_calls,calls)

    def test_task_cli_download_all_prints_only_durable_artifacts(self):
        artifacts=tuple(self._durable(index) for index in (1,2)); artifact_set=DurableAudioArtifactSet(
            provider_task_id="task-01",artifacts=artifacts,expected_artifact_count=2,complete=True)
        result=record(GenerationTaskStatus.SUCCEEDED).model_copy(update={"artifact_set":artifact_set}); engine=Mock(); engine.download_all_variants.return_value=result
        argv=["music_engine_task","--provider","fake","--task-id","task-01","--download-all","--output-dir","songs"]
        with patch("sys.argv",argv),patch("app.cli.music_engine_task.build_music_engine",return_value=engine),patch("builtins.print") as emit:
            self.assertEqual(task_cli(),0)
        engine.download_all_variants.assert_called_once_with("task-01",Path("songs")); output="\n".join(str(c.args[0]) for c in emit.call_args_list)
        self.assertIn("Variants downloaded: 2",output); self.assertIn("Variant: 2",output); self.assertNotIn("signed",output)

    def test_generate_download_all_delegates_once(self):
        root=Path(__file__).resolve().parents[1]/"examples"/"smoke"; artifacts=tuple(self._durable(index) for index in (1,2))
        result=MusicGenerationTaskRecord(provider="sunoapi_org",provider_task_id="paid-task",normalized_status="succeeded",
            created_at=NOW,updated_at=NOW,artifact_set=DurableAudioArtifactSet(provider_task_id="paid-task",artifacts=artifacts,expected_artifact_count=2,complete=True))
        engine=Mock(); engine.generate_all_variants.return_value=result
        argv=["music_generate","--lyrics",str(root/"lyrics-plan.json"),"--music-plan",str(root/"music-plan.json"),"--provider","sunoapi_org",
              "--output-dir","songs","--download-all","--confirm"]
        with patch("sys.argv",argv),patch("app.cli.music_generate.build_music_engine",return_value=engine),patch("builtins.print") as emit:
            self.assertEqual(generate_cli(),0)
        engine.generate_all_variants.assert_called_once(); engine.generate.assert_not_called()
        self.assertIn("Variants downloaded: 2","\n".join(str(c.args[0]) for c in emit.call_args_list))

    def _durable(self,index):
        from app.music import DurableAudioArtifact
        return DurableAudioArtifact(artifact_id=f"variant-{index}",local_path=Path(f"variant-{index:02d}.mp3"),
            byte_size=5,sha256=str(index)*64,content_type="audio/mpeg",variant_index=index)


if __name__=="__main__": unittest.main()
