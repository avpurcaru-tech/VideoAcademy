import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.models import GenerationTask, GenerationTaskStatus, VideoArtifact, VideoGenerationRequest, VideoRequest, VideoEnvironment, Camera, Transition
from app.services import (
    ArtifactRecord,
    DownloadedVideoArtifact,
    GenerationTaskRecord,
    MultipleDownloadableVideoArtifactsError,
    NoDownloadableVideoArtifactError,
    ProviderTaskIdMismatchError,
    TaskRegistry,
    UnknownVideoProviderError,
    VideoEngine,
    VideoEngineArtifactDownloadError,
    VideoTaskNotFoundError,
    VideoTaskNotSucceededError,
)


NOW = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)


class VideoEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.registry = TaskRegistry(Path(self.temporary.name) / "tasks")
        self.provider = FakeProvider()
        self.downloader = FakeDownloader()
        self.engine = VideoEngine(
            {"fake": self.provider}, self.registry, self.downloader, default_provider="fake"
        )

    def test_submit_delegates_once_and_persists_only_durable_fields(self) -> None:
        self.provider.task = task(status=GenerationTaskStatus.SUBMITTED)
        record = self.engine.submit(request())

        self.assertEqual(self.provider.submit_calls, 1)
        self.assertEqual(record, self.registry.load("task-01"))
        payload = json.loads((Path(self.temporary.name) / "tasks" / "task-01.json").read_text())
        serialized = json.dumps(payload)
        self.assertEqual(set(payload), {"provider", "provider_task_id", "external_correlation_id", "normalized_status", "created_at", "updated_at", "artifact"})
        self.assertNotIn("signed-secret", serialized)
        self.assertNotIn("prompt", serialized)

    def test_refresh_delegates_once_updates_status_and_preserves_artifact(self) -> None:
        artifact = ArtifactRecord(artifact_id="old", local_path=Path("old.mp4"), byte_size=2, sha256="a" * 64, content_type="video/mp4")
        self._store(status=GenerationTaskStatus.PROCESSING, artifact=artifact)
        self.provider.task = task(status=GenerationTaskStatus.SUCCEEDED)

        record = self.engine.refresh("task-01")

        self.assertEqual(self.provider.query_calls, 1)
        self.assertEqual(record.normalized_status, GenerationTaskStatus.SUCCEEDED)
        self.assertEqual(record.artifact, artifact)

    def test_refresh_rejects_provider_task_id_mismatch(self) -> None:
        self._store()
        self.provider.task = task(task_id="other")
        with self.assertRaises(ProviderTaskIdMismatchError):
            self.engine.refresh("task-01")

    def test_unknown_provider_and_missing_registry_are_explicit(self) -> None:
        other = VideoEngine({}, self.registry, self.downloader, default_provider="unknown")
        with self.assertRaises(UnknownVideoProviderError):
            other.submit(request())
        with self.assertRaises(VideoTaskNotFoundError):
            self.engine.refresh("missing")

    def test_download_requires_succeeded_and_refreshes_once(self) -> None:
        self._store()
        self.provider.task = task(status=GenerationTaskStatus.PROCESSING)
        with self.assertRaises(VideoTaskNotSucceededError):
            self.engine.download("task-01", Path("out.mp4"))
        self.assertEqual(self.provider.query_calls, 1)
        self.assertEqual(self.downloader.calls, 0)
        self.assertEqual(self.registry.load("task-01").normalized_status, GenerationTaskStatus.PROCESSING)

    def test_download_rejects_zero_and_multiple_video_artifacts(self) -> None:
        for artifacts, error_type in [([], NoDownloadableVideoArtifactError), ([video("one"), video("two")], MultipleDownloadableVideoArtifactsError)]:
            with self.subTest(error=error_type.__name__):
                self._store(replace=True)
                self.provider.task = task(status=GenerationTaskStatus.SUCCEEDED, artifacts=artifacts)
                with self.assertRaises(error_type):
                    self.engine.download("task-01", Path("out.mp4"))

    def test_successful_download_publishes_complete_metadata(self) -> None:
        self._store()
        self.provider.task = task(status=GenerationTaskStatus.SUCCEEDED, artifacts=[video("video-01")])
        record = self.engine.download("task-01", Path("out.mp4"))

        self.assertEqual(self.provider.query_calls, 1)
        self.assertEqual(self.downloader.calls, 1)
        self.assertEqual(record.artifact.artifact_id, "video-01")
        self.assertEqual(record.artifact.sha256, hashlib.sha256(b"video").hexdigest())
        self.assertEqual(record, self.registry.load("task-01"))

    def test_downloader_failure_does_not_publish_incomplete_metadata(self) -> None:
        self._store()
        self.provider.task = task(status=GenerationTaskStatus.SUCCEEDED, artifacts=[video("video-01")])
        self.downloader.error = RuntimeError("signed-secret-url credentials")
        with self.assertRaises(VideoEngineArtifactDownloadError) as caught:
            self.engine.download("task-01", Path("out.mp4"))
        self.assertIsNone(self.registry.load("task-01").artifact)
        self.assertNotIn("secret", str(caught.exception))

    def _store(self, status=GenerationTaskStatus.SUBMITTED, artifact=None, replace=False) -> None:
        record = GenerationTaskRecord(provider="fake", provider_task_id="task-01", external_correlation_id="external-01", normalized_status=status, created_at=NOW, updated_at=NOW, artifact=artifact)
        if replace and self.registry.exists("task-01"):
            self.registry.update(record)
        else:
            self.registry.create(record)


class FakeProvider:
    def __init__(self) -> None:
        self.task = task()
        self.submit_calls = 0
        self.query_calls = 0

    def submit_generation(self, generation_request):
        self.submit_calls += 1
        return self.task

    def get_task_by_id(self, provider_task_id):
        self.query_calls += 1
        return self.task

    def get_task_by_external_id(self, external_correlation_id):
        return self.task


class FakeDownloader:
    def __init__(self) -> None:
        self.calls = 0
        self.error = None

    def download_video_artifact(self, artifact, destination, *, overwrite=False):
        self.calls += 1
        if self.error:
            raise self.error
        return DownloadedVideoArtifact(artifact_id=artifact.artifact_id, local_path=destination, byte_size=5, sha256=hashlib.sha256(b"video").hexdigest(), content_type="video/mp4")


def task(task_id="task-01", status=GenerationTaskStatus.SUBMITTED, artifacts=None):
    return GenerationTask(external_task_id=task_id, provider_name="fake", provider_status=status.value, normalized_status=status, external_correlation_id="external-01", artifacts=artifacts or [], provider_metadata={"prompt": "secret", "url": "signed-secret"}, submitted_at=NOW, updated_at=NOW)


def video(artifact_id):
    return VideoArtifact(artifact_id=artifact_id, url=f"https://cdn.test/{artifact_id}?signed-secret", content_type="video/mp4")


def request():
    return VideoGenerationRequest(request_id="scene-01", video_request=VideoRequest(scene_number=1, duration_seconds=15, environment=VideoEnvironment(location_name="loc", location_description="desc", time_of_day="day", lighting_description="light", lighting_intensity="medium"), camera=Camera(shot_type="wide", description="wide"), transition=Transition(type="cut")))
