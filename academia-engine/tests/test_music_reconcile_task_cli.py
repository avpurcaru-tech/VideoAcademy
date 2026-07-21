import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

from app.cli.music_reconcile_task import main as cli_main
from app.models import GenerationTaskStatus
from app.music import GeneratedAudioArtifact,MusicEngine,MusicGenerationTask,MusicTaskRegistry


class ReconcileProvider:
    provider_name="sunoapi_org"
    def __init__(self): self.queries=[]
    def submit_generation(self,request): raise AssertionError("submit is forbidden")
    def get_task_by_id(self,task_id):
        self.queries.append(task_id)
        return MusicGenerationTask(provider=self.provider_name,provider_task_id=task_id,
            normalized_status=GenerationTaskStatus.SUCCEEDED,artifacts=(
                GeneratedAudioArtifact(artifact_id="variant-one",download_url="https://signed.invalid/one.mp3",content_type="audio/mpeg"),
                GeneratedAudioArtifact(artifact_id="variant-two",download_url="https://signed.invalid/two.mp3",content_type="audio/mpeg")))


class MusicReconcileTaskTests(unittest.TestCase):
    def test_engine_queries_once_and_adopts_without_transient_artifacts(self):
        provider=ReconcileProvider()
        with tempfile.TemporaryDirectory() as directory:
            registry=MusicTaskRegistry(Path(directory)/"tasks")
            engine=MusicEngine({"sunoapi_org":provider},registry,Mock(),default_provider="sunoapi_org")
            record=engine.reconcile_existing_task("sunoapi_org","known-task-1")
            durable=registry.load("known-task-1")
            manifest=(Path(directory)/"tasks"/"known-task-1.json").read_text(encoding="utf-8")
        self.assertEqual(provider.queries,["known-task-1"])
        self.assertEqual(record,durable); self.assertIsNone(durable.artifact); self.assertIsNone(durable.artifact_set)
        self.assertEqual(durable.provider_artifact_ids,("variant-one","variant-two"))
        self.assertNotIn("signed.invalid",manifest); self.assertNotIn("audio_url",manifest)

    def test_cli_delegates_once_and_prints_only_safe_task_state(self):
        engine=Mock(); engine.reconcile_existing_task.return_value=Mock(
            provider="sunoapi_org",provider_task_id="known-task-1",
            normalized_status=GenerationTaskStatus.PROCESSING)
        argv=["music_reconcile_task","--provider","sunoapi_org","--task-id","known-task-1"]
        with patch("sys.argv",argv),patch("app.cli.music_reconcile_task.load_application_environment"),patch(
                "app.cli.music_reconcile_task.build_music_engine",return_value=engine),patch("builtins.print") as emit:
            self.assertEqual(cli_main(),0)
        engine.reconcile_existing_task.assert_called_once_with("sunoapi_org","known-task-1")
        output="\n".join(str(call.args[0]) for call in emit.call_args_list)
        self.assertEqual(output,"Provider: sunoapi_org\nProvider task ID: known-task-1\nStatus: processing\nTask reconciliation: succeeded")
        for forbidden in ("audioUrl","https://","Authorization","lyrics"): self.assertNotIn(forbidden,output)


if __name__=="__main__": unittest.main()
