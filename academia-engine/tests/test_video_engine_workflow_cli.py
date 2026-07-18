from datetime import datetime, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

from app.cli.video_engine_generate import main as generate_main
from app.cli.video_engine_task import main as task_main
from app.models import GenerationTaskStatus
from app.services import ArtifactRecord, GenerationTaskRecord, VideoPollingPolicy


class VideoEngineWorkflowCliTests(unittest.TestCase):
    def test_generate_cli_calls_workflow_and_prints_sanitized_output(self) -> None:
        arguments = ["video_engine_generate", "--provider", "kling", "--output", "output.mp4", "--interval", "2", "--timeout", "120"]
        with patch("sys.argv", arguments), patch("app.cli.video_engine_generate.build_video_engine") as builder, patch("app.cli.video_engine_generate.print") as output:
            builder.return_value.generate.return_value = record()
            self.assertEqual(generate_main(), 0)
        call = builder.return_value.generate.call_args
        self.assertEqual(call.args[1], Path("output.mp4"))
        self.assertEqual(call.args[2], VideoPollingPolicy(interval_seconds=2, timeout_seconds=120))
        self.assertEqual(call.kwargs, {"provider": "kling"})
        assert_sanitized(self, output)

    def test_resume_cli_calls_workflow_and_prints_sanitized_output(self) -> None:
        arguments = ["video_engine_task", "--provider", "kling", "--task-id", "task-01", "--resume", "--download", "output.mp4", "--interval", "2", "--timeout", "120"]
        with patch("sys.argv", arguments), patch("app.cli.video_engine_task.build_video_engine") as builder, patch("app.cli.video_engine_task.print") as output:
            builder.return_value.resume.return_value = record()
            self.assertEqual(task_main(), 0)
        builder.return_value.resume.assert_called_once_with("task-01", Path("output.mp4"), VideoPollingPolicy(interval_seconds=2, timeout_seconds=120))
        assert_sanitized(self, output)


def record():
    now = datetime(2026, 7, 18, tzinfo=timezone.utc)
    return GenerationTaskRecord(provider="kling", provider_task_id="task-01", external_correlation_id="external-01", normalized_status=GenerationTaskStatus.SUCCEEDED, created_at=now, updated_at=now, artifact=ArtifactRecord(artifact_id="video-01", local_path=Path("output.mp4"), byte_size=5, sha256="a" * 64, content_type="video/mp4"))


def assert_sanitized(test_case, output):
    text = "\n".join(call.args[0] for call in output.call_args_list)
    test_case.assertIn("Provider: kling", text)
    for forbidden in ("signed", "prompt", "Authorization", "billing", "api-key"):
        test_case.assertNotIn(forbidden, text)
