import unittest
from unittest.mock import patch

from app.cli.kling_task_show import main
from app.models import GenerationTaskStatus
from app.services import ArtifactRecord, GenerationTaskRecord


class KlingTaskShowCliTests(unittest.TestCase):
    def test_cli_prints_only_durable_manifest_fields(self) -> None:
        record = GenerationTaskRecord.model_validate(
            {
                "provider": "kling",
                "provider_task_id": "task-01",
                "external_correlation_id": "external-01",
                "normalized_status": "succeeded",
                "created_at": "2026-07-18T10:00:00Z",
                "updated_at": "2026-07-18T10:01:00Z",
                "artifact": {
                    "artifact_id": "video-01",
                    "local_path": "storage/generated/video.mp4",
                    "byte_size": 5,
                    "sha256": "a" * 64,
                    "content_type": "video/mp4",
                },
            }
        )
        with patch("sys.argv", ["kling_task_show", "--task-id", "task-01"]), patch(
            "app.cli.kling_task_show.TaskRegistry"
        ) as registry_class, patch("app.cli.kling_task_show.print") as print_mock:
            registry_class.return_value.load.return_value = record

            self.assertEqual(main(), 0)

        output = "\n".join(call.args[0] for call in print_mock.call_args_list)
        self.assertIn("Provider: kling", output)
        self.assertIn("Task ID: task-01", output)
        self.assertIn("Artifact ID: video-01", output)
        self.assertNotIn("url", output.lower())
        self.assertNotIn("authorization", output.lower())
