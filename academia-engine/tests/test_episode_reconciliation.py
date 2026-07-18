import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from app.cli.episode_reconcile_task import main as reconcile_main
from app.production import (
    EpisodeProductionReconciler,
    EpisodeProductionStatus,
    EpisodeReconciliationConflictError,
    EpisodeReconciliationPreconditionError,
    GenerationRequestReference,
    ProductionRecord,
    ProductionRegistry,
)
from app.services import ArtifactRecord, GenerationTaskRecord


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class EpisodeReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.registry = ProductionRegistry(self.root / "productions")
        self.registry.create(record(self.root))
        self.engine = FakeReconciliationEngine()
        self.service = EpisodeProductionReconciler(self.engine, self.registry, clock=lambda: NOW)

    def tearDown(self): self.temp.cleanup()

    def test_verified_orphan_is_queried_once_attached_without_submit_and_failure_becomes_pending(self):
        updated = self.service.reconcile_provider_task("episode-001", "scene-0001", "907594518684373074")
        scene = updated.scenes[0]
        self.assertEqual(self.engine.reconcile_calls, [("kling", "907594518684373074")])
        self.assertEqual(self.engine.submit_calls, 0)
        self.assertEqual(scene.provider_task_id, "907594518684373074")
        self.assertEqual(scene.external_correlation_id, "correlation-safe")
        self.assertEqual(scene.normalized_status.value, "succeeded")
        self.assertEqual(scene.generation_request_reference, GenerationRequestReference(reference_id="episode-001-scene-0001"))
        self.assertEqual(updated.status, EpisodeProductionStatus.PENDING)
        manifest = (self.root / "productions/episode-001.json").read_text()
        for forbidden in ("signed", "https://", "prompt", "Authorization", "provider_metadata"):
            self.assertNotIn(forbidden, manifest)

    def test_same_task_is_idempotent_and_different_task_conflicts(self):
        self.service.reconcile_provider_task("episode-001", "scene-0001", "task-one")
        self.service.reconcile_provider_task("episode-001", "scene-0001", "task-one")
        self.assertEqual(len(self.engine.reconcile_calls), 1)
        with self.assertRaises(EpisodeReconciliationConflictError):
            self.service.reconcile_provider_task("episode-001", "scene-0001", "task-two")

    def test_scene_with_artifact_rejects_attachment(self):
        original = self.registry.load("episode-001"); scene = original.scenes[0].model_copy(update={"local_path": self.root/"scene.mp4", "artifact_id":"a", "sha256":"a"*64})
        self.registry.update(original.model_copy(update={"scenes": (scene, original.scenes[1])}))
        with self.assertRaises(EpisodeReconciliationPreconditionError):
            self.service.reconcile_provider_task("episode-001", "scene-0001", "task-one")
        self.assertEqual(self.engine.reconcile_calls, [])

    def test_identity_mismatch_is_rejected_without_production_attachment(self):
        self.engine.returned_task_id = "other-task"
        from app.production import EpisodeReconciliationProviderError
        with self.assertRaises(EpisodeReconciliationProviderError):
            self.service.reconcile_provider_task("episode-001", "scene-0001", "requested-task")
        self.assertIsNone(self.registry.load("episode-001").scenes[0].provider_task_id)

    def test_targeted_recovery_downloads_only_attached_scene_and_persists_artifact(self):
        self.service.reconcile_provider_task("episode-001", "scene-0001", "task-one")
        updated = self.service.recover_scene("episode-001", "scene-0001")
        self.assertEqual(self.engine.download_calls, [("task-one", self.root / "scenes/scene-0001.mp4")])
        self.assertEqual(self.engine.submit_calls, 0)
        self.assertEqual(updated.scenes[0].local_path, self.root / "scenes/scene-0001.mp4")
        self.assertIsNone(updated.scenes[1].provider_task_id)

    def test_reconciliation_cli_prints_only_sanitized_durable_fields(self):
        updated = self.service.reconcile_provider_task("episode-001", "scene-0001", "task-one")
        reconciler = Mock(); reconciler.reconcile_provider_task.return_value = updated
        argv = ["episode_reconcile_task", "--production-id", "episode-001", "--scene-id", "scene-0001", "--task-id", "task-one"]
        with patch("sys.argv", argv), patch("app.cli.episode_reconcile_task.build_reconciler", return_value=reconciler), patch("builtins.print") as emit:
            self.assertEqual(reconcile_main(), 0)
        output = " ".join(str(call.args[0]) for call in emit.call_args_list)
        self.assertIn("Reconciliation: succeeded", output); self.assertIn("task-one", output)
        for forbidden in ("signed", "prompt", "Authorization", "credential", "raw"):
            self.assertNotIn(forbidden, output)


class FakeReconciliationEngine:
    def __init__(self): self.reconcile_calls=[]; self.download_calls=[]; self.submit_calls=0; self.returned_task_id=None
    def reconcile_existing_task(self, provider, task_id):
        self.reconcile_calls.append((provider, task_id))
        return GenerationTaskRecord(provider=provider, provider_task_id=self.returned_task_id or task_id,
            external_correlation_id="correlation-safe", normalized_status="succeeded", created_at=NOW, updated_at=NOW)
    def submit(self, *args, **kwargs): self.submit_calls += 1; raise AssertionError("submit must never be called")
    def download(self, task_id, destination):
        self.download_calls.append((task_id, destination)); destination.parent.mkdir(parents=True, exist_ok=True); destination.write_bytes(b"mp4")
        artifact = ArtifactRecord(artifact_id="artifact-one", local_path=destination, byte_size=3, sha256="a"*64, content_type="video/mp4")
        return GenerationTaskRecord(provider="kling", provider_task_id=task_id, external_correlation_id="correlation-safe",
            normalized_status="succeeded", created_at=NOW, updated_at=NOW, artifact=artifact)


def record(root):
    scenes = tuple({"scene_id":f"scene-{index:04d}", "order":index-1,
        "generation_request_reference":{"reference_id":f"episode-001-scene-{index:04d}"}} for index in (1,2))
    return ProductionRecord(production_id="episode-001", status="failed", provider="kling", scenes=scenes,
        scene_output_directory=root/"scenes", final_output_path=root/"final.mp4", media_workspace=root/"media",
        transition_policy={"kind":"fade", "duration_seconds":0.5}, created_at=NOW, updated_at=NOW)


if __name__ == "__main__": unittest.main()
