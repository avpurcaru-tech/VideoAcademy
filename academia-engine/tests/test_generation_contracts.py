from datetime import datetime, timezone
import unittest

from app.models import (
    GenerationTask,
    GenerationTaskStatus,
    VideoArtifact,
    VideoGenerationResult,
)


class GenerationContractTests(unittest.TestCase):
    def test_generation_task_uses_normalized_status(self) -> None:
        task = GenerationTask(
            request_id="scene-01",
            external_task_id="provider-task-01",
            provider_name="kling",
            provider_status="processing",
            normalized_status=GenerationTaskStatus.PROCESSING,
            submitted_at=datetime.now(timezone.utc),
        )

        self.assertEqual(task.normalized_status, GenerationTaskStatus.PROCESSING)

    def test_result_supports_provider_task_errors_and_artifacts(self) -> None:
        result = VideoGenerationResult(
            request_id="scene-01",
            scene_number=1,
            provider_name="kling",
            external_task_id="provider-task-01",
            provider_status="succeed",
            normalized_status=GenerationTaskStatus.SUCCEEDED,
            artifacts=[
                VideoArtifact(
                    artifact_id="work-01",
                    url="https://example.com/video.mp4",
                    content_type="video/mp4",
                )
            ],
            submitted_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            status="completed",
        )

        self.assertEqual(result.artifacts[0].artifact_id, "work-01")
        self.assertEqual(result.normalized_status, GenerationTaskStatus.SUCCEEDED)
