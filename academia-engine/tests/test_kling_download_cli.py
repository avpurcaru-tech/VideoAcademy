import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.cli.kling_download_test import _select_single_video_artifact, main
from app.models import GenerationTask, GenerationTaskStatus, VideoArtifact
from app.services import VideoArtifactAmbiguityError, VideoArtifactNotFoundError


class KlingDownloadCliTests(unittest.TestCase):
    def test_cli_downloads_exactly_one_artifact_without_printing_signed_url(self) -> None:
        artifact = VideoArtifact(
            artifact_id="video-01",
            url="https://cdn.example.test/signed-secret-url",
        )
        task = GenerationTask(
            request_id=None,
            external_task_id="task-01",
            provider_name="kling",
            provider_status="succeeded",
            normalized_status=GenerationTaskStatus.SUCCEEDED,
            artifacts=[artifact],
        )
        with TemporaryDirectory() as directory, patch(
            "sys.argv", ["kling_download_test", "--task-id", "task-01", "--output", str(Path(directory) / "out.mp4")]
        ), patch("app.cli.kling_download_test.KlingProvider") as provider_class, patch(
            "app.cli.kling_download_test.KlingVideoArtifactDownloader"
        ) as downloader_class, patch("app.cli.kling_download_test.sync_task_record") as registry_sync, patch(
            "app.cli.kling_download_test.print"
        ) as print_mock:
            provider_class.return_value.get_task_by_id.return_value = task
            downloader_class.return_value.download_video_artifact.return_value = type(
                "Downloaded", (), {
                    "artifact_id": "video-01",
                    "local_path": Path(directory) / "out.mp4",
                    "byte_size": 5,
                    "sha256": hashlib.sha256(b"video").hexdigest(),
                }
            )()

            self.assertEqual(main(), 0)

        downloader_class.return_value.download_video_artifact.assert_called_once()
        registry_sync.assert_called_once()
        output = "\n".join(call.args[0] for call in print_mock.call_args_list)
        self.assertIn("Kling task ID: task-01", output)
        self.assertIn("Video artifact ID: video-01", output)
        self.assertNotIn("signed-secret-url", output)

    def test_zero_and_multiple_artifacts_are_explicit_errors(self) -> None:
        with self.assertRaises(VideoArtifactNotFoundError):
            _select_single_video_artifact([])
        with self.assertRaises(VideoArtifactAmbiguityError):
            _select_single_video_artifact(
                [
                    VideoArtifact(artifact_id="one", url="https://example.test/one"),
                    VideoArtifact(artifact_id="two", url="https://example.test/two"),
                ]
            )
