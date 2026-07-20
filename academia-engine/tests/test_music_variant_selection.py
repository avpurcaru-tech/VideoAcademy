import tempfile
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import Mock,patch

from app.cli.music_engine_task import main as task_cli
from app.cli.music_generate import main as generate_cli
from app.models import GenerationTaskStatus
from app.music import (AtomicAudioArtifactDownloader,DurableAudioArtifact,GeneratedAudioArtifact,GeneratedMusicVariant,
    MusicArtifactCardinalityError,MusicEngine,MusicGenerationTaskRecord,MusicPollingPolicy,MusicTaskRegistry,
    MusicVariantIndexError,MusicVariantSelectionRequiredError)
from tests.test_music_generation_foundation import FakeProvider,NOW,record,request,task


def variants_task():
    return task(GenerationTaskStatus.SUCCEEDED,(
        GeneratedAudioArtifact(artifact_id="variant-one",download_url="https://signed.invalid/one.mp3",content_type="audio/mpeg"),
        GeneratedAudioArtifact(artifact_id="variant-two",download_url="https://signed.invalid/two.mp3",content_type="audio/mpeg")))


class MusicVariantSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name); self.registry=MusicTaskRegistry(self.root/"tasks")
        self.registry.create(record(GenerationTaskStatus.SUCCEEDED)); self.provider=FakeProvider(); self.downloaded=[]
        def reader(artifact): self.downloaded.append(artifact.artifact_id); return artifact.artifact_id.encode()
        self.engine=MusicEngine({"fake":self.provider},self.registry,AtomicAudioArtifactDownloader(reader),default_provider="fake")
    def tearDown(self): self.temp.cleanup()

    def test_list_variants_queries_once_preserves_order_and_contains_no_urls(self):
        self.provider.queries=deque([variants_task()]); listed=self.engine.list_variants("task-01")
        self.assertEqual(self.provider.query_calls,1); self.assertEqual([v.variant_index for v in listed],[1,2])
        self.assertEqual([v.artifact_id for v in listed],["variant-one","variant-two"])
        self.assertNotIn("download_url",listed[0].model_dump()); self.assertNotIn("signed",repr(listed))

    def test_each_explicit_variant_downloads_only_selected_and_persists_metadata(self):
        for index,artifact_id in ((1,"variant-one"),(2,"variant-two")):
            registry=MusicTaskRegistry(self.root/f"tasks-{index}"); registry.create(record(GenerationTaskStatus.SUCCEEDED))
            provider=FakeProvider(); provider.queries=deque([variants_task()]); downloaded=[]
            engine=MusicEngine({"fake":provider},registry,AtomicAudioArtifactDownloader(lambda artifact:(downloaded.append(artifact.artifact_id) or b"audio")),default_provider="fake")
            result=engine.download_variant("task-01",index,self.root/f"selected-{index}.mp3")
            self.assertEqual(downloaded,[artifact_id]); self.assertEqual(result.artifact.artifact_id,artifact_id)
            raw=(self.root/f"tasks-{index}"/"task-01.json").read_text(encoding="utf-8")
            self.assertNotIn("signed.invalid",raw); self.assertNotIn("download_url",raw)

    def test_invalid_indices_rejected_without_download(self):
        for index in (0,-1,3):
            self.provider.queries=deque([variants_task()])
            with self.subTest(index=index),self.assertRaises(MusicVariantIndexError):
                self.engine.download_variant("task-01",index,self.root/f"bad-{index}.mp3")
        self.assertEqual(self.downloaded,[])

    def test_generic_download_remains_strict(self):
        self.provider.queries=deque([variants_task()])
        with self.assertRaises(MusicArtifactCardinalityError): self.engine.download("task-01",self.root/"generic.mp3")
        self.assertEqual(self.downloaded,[])

    def test_resume_requires_selection_then_returns_selected_artifact_without_provider(self):
        self.provider.queries=deque([variants_task()])
        with self.assertRaises(MusicVariantSelectionRequiredError) as raised:
            self.engine.resume("task-01",self.root/"resume.mp3",MusicPollingPolicy(interval_seconds=1,timeout_seconds=5))
        self.assertEqual(raised.exception.available_variants,2); self.assertEqual(self.downloaded,[])
        durable=DurableAudioArtifact(artifact_id="variant-two",local_path=self.root/"selected.mp3",byte_size=5,sha256="a"*64,content_type="audio/mpeg")
        self.registry.update(record(GenerationTaskStatus.SUCCEEDED,durable)); before=self.provider.query_calls
        self.assertEqual(self.engine.resume("task-01",self.root/"ignored.mp3",MusicPollingPolicy(interval_seconds=1,timeout_seconds=5)).artifact,durable)
        self.assertEqual(self.provider.query_calls,before)

    def test_variant_cli_listing_and_selection_are_sanitized(self):
        engine=Mock(); engine.list_variants.return_value=(GeneratedMusicVariant(variant_index=1,artifact_id="one",content_type="audio/mpeg"),GeneratedMusicVariant(variant_index=2,artifact_id="two",content_type="audio/mpeg"))
        with patch("sys.argv",["music_engine_task","--provider","fake","--task-id","task-01","--variants"]),patch("app.cli.music_engine_task.build_music_engine",return_value=engine),patch("builtins.print") as emit:
            self.assertEqual(task_cli(),0)
        output="\n".join(str(c.args[0]) for c in emit.call_args_list); self.assertIn("Variants: 2",output); self.assertNotIn("http",output)
        durable=DurableAudioArtifact(artifact_id="two",local_path=Path("selected.mp3"),byte_size=5,sha256="b"*64,content_type="audio/mpeg")
        engine.download_variant.return_value=MusicGenerationTaskRecord(provider="fake",provider_task_id="task-01",normalized_status="succeeded",created_at=NOW,updated_at=NOW,artifact=durable)
        with patch("sys.argv",["music_engine_task","--provider","fake","--task-id","task-01","--select-variant","2","--download","selected.mp3"]),patch("app.cli.music_engine_task.build_music_engine",return_value=engine),patch("builtins.print") as emit:
            self.assertEqual(task_cli(),0)
        engine.download_variant.assert_called_once_with("task-01",2,Path("selected.mp3")); output="\n".join(str(c.args[0]) for c in emit.call_args_list)
        self.assertIn("Selected variant: 2",output); self.assertIn("Saved path: selected.mp3",output)

    def test_generate_cli_reports_successful_selection_required_state(self):
        root=Path(__file__).resolve().parents[1]/"examples"/"smoke"; engine=Mock()
        engine.generate.side_effect=MusicVariantSelectionRequiredError("paid-task-1",2)
        argv=["music_generate","--lyrics",str(root/"lyrics-plan.json"),"--music-plan",str(root/"music-plan.json"),"--provider","sunoapi_org","--output","unused.mp3","--confirm"]
        with patch("sys.argv",argv),patch("app.cli.music_generate.build_music_engine",return_value=engine),patch("builtins.print") as emit:
            self.assertEqual(generate_cli(),3)
        output="\n".join(str(c.args[0]) for c in emit.call_args_list)
        self.assertIn("Provider task ID: paid-task-1",output); self.assertIn("Music generation succeeded with 2 variants.",output)
        self.assertIn("Select a variant before download.",output); self.assertNotIn("signed",output)


if __name__=="__main__": unittest.main()
