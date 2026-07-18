import json
import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.models import GenerationTask, GenerationTaskStatus
from app.services import (
    ArtifactRecord,
    GenerationTaskRecord,
    TaskRegistry,
    TaskRegistryAlreadyExistsError,
    TaskRegistryCorruptedManifestError,
    TaskRegistryError,
    TaskRegistryNotFoundError,
)
from app.cli.kling_task_registry import sync_task_record


class TaskRegistryTests(unittest.TestCase):
    def test_create_load_exists_list_and_update_overwrite_one_manifest(self) -> None:
        with TemporaryDirectory() as directory:
            registry = TaskRegistry(Path(directory) / "tasks")
            record = self._record()

            registry.create(record)

            self.assertTrue(registry.exists("task-01"))
            self.assertEqual(registry.load("task-01"), record)
            self.assertEqual(registry.list(), [record])
            manifest = Path(directory) / "tasks" / "task-01.json"
            self.assertTrue(manifest.is_file())

            updated = record.model_copy(
                update={
                    "normalized_status": GenerationTaskStatus.SUCCEEDED,
                    "updated_at": datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc),
                    "artifact": ArtifactRecord(
                        artifact_id="video-01",
                        local_path=Path("storage/generated/video.mp4"),
                        byte_size=5,
                        sha256="a" * 64,
                        content_type="video/mp4",
                    ),
                }
            )
            registry.update(updated)

            self.assertEqual(registry.load("task-01"), updated)
            self.assertEqual(len(list((Path(directory) / "tasks").glob("*.json"))), 1)

    def test_create_rejects_existing_and_update_rejects_missing(self) -> None:
        with TemporaryDirectory() as directory:
            registry = TaskRegistry(Path(directory))
            record = self._record()
            registry.create(record)

            with self.assertRaises(TaskRegistryAlreadyExistsError):
                registry.create(record)
            with self.assertRaises(TaskRegistryNotFoundError):
                TaskRegistry(Path(directory) / "other").update(record)

    def test_atomic_write_cleans_part_file_after_interruption(self) -> None:
        with TemporaryDirectory() as directory:
            registry = TaskRegistry(Path(directory) / "tasks")
            with patch("app.services.task_registry.os.replace", side_effect=OSError("interrupted")):
                with self.assertRaises(TaskRegistryError):
                    registry.create(self._record())

            self.assertFalse((Path(directory) / "tasks" / "task-01.json").exists())
            self.assertFalse((Path(directory) / "tasks" / "task-01.json.part").exists())

    def test_corrupted_and_missing_manifests_raise_explicit_errors(self) -> None:
        with TemporaryDirectory() as directory:
            registry = TaskRegistry(Path(directory) / "tasks")
            with self.assertRaises(TaskRegistryNotFoundError):
                registry.load("task-01")
            registry._root.mkdir(parents=True)
            (registry._root / "task-01.json").write_text("{not-json", encoding="utf-8")

            with self.assertRaises(TaskRegistryCorruptedManifestError):
                registry.load("task-01")

    def test_serialization_contains_only_durable_fields(self) -> None:
        with TemporaryDirectory() as directory:
            registry = TaskRegistry(Path(directory) / "tasks")
            registry.create(self._record())
            payload = json.loads((Path(directory) / "tasks" / "task-01.json").read_text(encoding="utf-8"))

        self.assertEqual(
            set(payload),
            {
                "provider",
                "provider_task_id",
                "external_correlation_id",
                "normalized_status",
                "created_at",
                "updated_at",
                "artifact",
            },
        )
        self.assertNotIn("url", json.dumps(payload))
        self.assertNotIn("prompt", json.dumps(payload))
        self.assertNotIn("billing", json.dumps(payload))

    def test_sync_creates_then_updates_preserving_created_at_and_artifact(self) -> None:
        with TemporaryDirectory() as directory:
            registry = TaskRegistry(Path(directory) / "tasks")
            task = GenerationTask(
                request_id="scene-01",
                external_task_id="task-01",
                provider_name="kling",
                provider_status="submitted",
                normalized_status=GenerationTaskStatus.SUBMITTED,
                external_correlation_id="external-01",
                submitted_at=datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc),
                updated_at=datetime(2026, 7, 18, 10, 1, tzinfo=timezone.utc),
            )
            created = sync_task_record(task, registry=registry)
            task.normalized_status = GenerationTaskStatus.SUCCEEDED
            task.updated_at = datetime(2026, 7, 18, 10, 2, tzinfo=timezone.utc)
            updated = sync_task_record(task, registry=registry)

            self.assertEqual(updated.created_at, created.created_at)
            self.assertEqual(updated.normalized_status, GenerationTaskStatus.SUCCEEDED)

    @staticmethod
    def _record() -> GenerationTaskRecord:
        return GenerationTaskRecord(
            provider="kling",
            provider_task_id="task-01",
            external_correlation_id="external-01",
            normalized_status=GenerationTaskStatus.SUBMITTED,
            created_at=datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 7, 18, 10, 1, tzinfo=timezone.utc),
        )
