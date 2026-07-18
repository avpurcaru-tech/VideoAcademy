from datetime import datetime, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

from app.cli.video_engine_task import main
from app.models import GenerationTaskStatus
from app.services import (
    ArtifactRecord,
    GenerationTaskRecord,
    VideoEngineTimeoutError,
    VideoPollingPolicy,
)


class VideoEngineCliTests(unittest.TestCase):
    def test_refresh_calls_video_engine_and_prints_only_sanitized_fields(self) -> None:
        record = self._record()
        with patch("sys.argv", ["video_engine_task", "--provider", "kling", "--task-id", "task-01", "--refresh"]), patch("app.cli.video_engine_task.build_video_engine") as builder, patch("app.cli.video_engine_task.print") as output:
            builder.return_value.refresh.return_value = record
            self.assertEqual(main(), 0)
        builder.return_value.refresh.assert_called_once_with("task-01")
        text = "\n".join(call.args[0] for call in output.call_args_list)
        self.assertIn("Provider: kling", text)
        self.assertIn("SHA-256: " + "a" * 64, text)
        self.assertNotIn("signed-secret", text)
        self.assertNotIn("billing", text)

    def test_download_calls_video_engine_not_provider(self) -> None:
        with patch("sys.argv", ["video_engine_task", "--provider", "kling", "--task-id", "task-01", "--download", "out.mp4"]), patch("app.cli.video_engine_task.build_video_engine") as builder, patch("app.cli.video_engine_task.print"):
            builder.return_value.download.return_value = self._record()
            self.assertEqual(main(), 0)
        builder.return_value.download.assert_called_once_with("task-01", Path("out.mp4"))

    def test_wait_uses_policy_and_prints_only_sanitized_record(self) -> None:
        with patch("sys.argv", ["video_engine_task", "--provider", "kling", "--task-id", "task-01", "--wait", "--interval", "2", "--timeout", "10"]), patch("app.cli.video_engine_task.build_video_engine") as builder, patch("app.cli.video_engine_task.print") as output:
            builder.return_value.wait_until_terminal.return_value = self._record()
            self.assertEqual(main(), 0)
        policy = builder.return_value.wait_until_terminal.call_args.args[1]
        self.assertEqual(policy, VideoPollingPolicy(interval_seconds=2, timeout_seconds=10))
        text = "\n".join(call.args[0] for call in output.call_args_list)
        self.assertNotIn("signed", text)
        self.assertNotIn("Authorization", text)
        self.assertNotIn("billing", text)

    def test_wait_and_download_and_timeout_use_safe_service_boundary(self) -> None:
        arguments = ["video_engine_task", "--provider", "kling", "--task-id", "task-01", "--wait", "--interval", "1", "--timeout", "5", "--download", "out.mp4"]
        with patch("sys.argv", arguments), patch("app.cli.video_engine_task.build_video_engine") as builder, patch("app.cli.video_engine_task.print"):
            builder.return_value.wait_and_download.return_value = self._record()
            self.assertEqual(main(), 0)
        builder.return_value.wait_and_download.assert_called_once()

        with patch("sys.argv", arguments[:-2]), patch("app.cli.video_engine_task.build_video_engine") as builder, patch("app.cli.video_engine_task.print") as output:
            builder.return_value.wait_until_terminal.side_effect = VideoEngineTimeoutError("signed-url secret")
            self.assertEqual(main(), 1)
        self.assertEqual(output.call_args.args[0], "Video polling timed out.")

    @staticmethod
    def _record():
        now = datetime(2026, 7, 18, tzinfo=timezone.utc)
        return GenerationTaskRecord(provider="kling", provider_task_id="task-01", external_correlation_id="external-01", normalized_status=GenerationTaskStatus.SUCCEEDED, created_at=now, updated_at=now, artifact=ArtifactRecord(artifact_id="video-01", local_path=Path("out.mp4"), byte_size=5, sha256="a" * 64, content_type="video/mp4"))
