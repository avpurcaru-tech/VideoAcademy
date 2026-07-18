from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.models import GenerationTask, GenerationTaskStatus, VideoArtifact
from app.services import (
    ArtifactRecord,
    DownloadedVideoArtifact,
    GenerationTaskRecord,
    TaskRegistry,
    VideoEngine,
    VideoEngineArtifactDownloadError,
    VideoEngineAttemptsExceededError,
    VideoEngineTaskFailedError,
    VideoEngineTimeoutError,
    VideoPollingPolicy,
    VideoProviderOperationError,
    VideoTaskNotFoundError,
)
from tests.test_video_engine_orchestrator import request


NOW = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)
POLICY = VideoPollingPolicy(interval_seconds=2, timeout_seconds=10)


class VideoEngineWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.registry = TaskRegistry(Path(self.temporary.name) / "tasks")
        self.provider = WorkflowProvider()
        self.downloader = WorkflowDownloader()
        self.clock = FakeClock()
        self.engine = VideoEngine(
            {"fake": self.provider},
            self.registry,
            self.downloader,
            default_provider="fake",
            monotonic_clock=self.clock.monotonic,
            sleeper=self.clock.sleep,
        )

    def test_generate_submits_once_waits_and_downloads_with_correlation_preserved(self) -> None:
        self.provider.submitted = task(GenerationTaskStatus.SUBMITTED)
        succeeded = task(GenerationTaskStatus.SUCCEEDED, [video()])
        self.provider.queries = deque([task(GenerationTaskStatus.PROCESSING), succeeded, succeeded])

        result = self.engine.generate(request(), Path("explicit-output.mp4"), POLICY)

        self.assertEqual(self.provider.submit_calls, 1)
        self.assertEqual(self.provider.query_calls, 3)
        self.assertEqual(self.downloader.calls, 1)
        self.assertEqual(result.provider, "fake")
        self.assertEqual(result.provider_task_id, "task-01")
        self.assertEqual(result.external_correlation_id, "correlation-01")
        self.assertEqual(result.artifact.local_path, Path("explicit-output.mp4"))
        self.assertEqual(result, self.registry.load("task-01"))

    def test_generate_immediate_succeeded_downloads_once(self) -> None:
        succeeded = task(GenerationTaskStatus.SUCCEEDED, [video()])
        self.provider.submitted = succeeded
        self.provider.queries = deque([succeeded, succeeded])
        self.engine.generate(request(), Path("out.mp4"), POLICY)
        self.assertEqual(self.provider.submit_calls, 1)
        self.assertEqual(self.downloader.calls, 1)
        self.assertEqual(self.clock.sleeps, [])

    def test_generate_failed_terminal_does_not_download(self) -> None:
        self.provider.submitted = task(GenerationTaskStatus.SUBMITTED)
        self.provider.queries = deque([task(GenerationTaskStatus.FAILED)])
        with self.assertRaises(VideoEngineTaskFailedError):
            self.engine.generate(request(), Path("out.mp4"), POLICY)
        self.assertEqual(self.provider.submit_calls, 1)
        self.assertEqual(self.downloader.calls, 0)

    def test_submit_failure_stops_before_registry_poll_and_download(self) -> None:
        self.provider.submit_error = RuntimeError("provider payload signed-secret")
        with self.assertRaises(VideoProviderOperationError):
            self.engine.generate(request(), Path("out.mp4"), POLICY)
        self.assertEqual(self.provider.submit_calls, 1)
        self.assertEqual(self.provider.query_calls, 0)
        self.assertEqual(self.downloader.calls, 0)
        self.assertEqual(self.registry.list(), [])

    def test_polling_failure_stops_without_download(self) -> None:
        self.provider.submitted = task(GenerationTaskStatus.SUBMITTED)
        self.provider.queries = deque([RuntimeError("Authorization secret")])
        with self.assertRaises(VideoProviderOperationError):
            self.engine.generate(request(), Path("out.mp4"), POLICY)
        self.assertEqual(self.provider.submit_calls, 1)
        self.assertEqual(self.provider.query_calls, 1)
        self.assertEqual(self.downloader.calls, 0)

    def test_timeout_and_attempt_exhaustion_stop_before_download(self) -> None:
        for policy, error in [
            (VideoPollingPolicy(interval_seconds=5, timeout_seconds=1), VideoEngineTimeoutError),
            (VideoPollingPolicy(interval_seconds=1, timeout_seconds=10, max_attempts=1), VideoEngineAttemptsExceededError),
        ]:
            with self.subTest(error=error.__name__):
                self._reset()
                self.provider.submitted = task(GenerationTaskStatus.SUBMITTED)
                self.provider.queries = deque([task(GenerationTaskStatus.PROCESSING)])
                with self.assertRaises(error):
                    self.engine.generate(request(), Path("out.mp4"), policy)
                self.assertEqual(self.downloader.calls, 0)

    def test_download_failure_leaves_succeeded_record_without_artifact(self) -> None:
        succeeded = task(GenerationTaskStatus.SUCCEEDED, [video()])
        self.provider.submitted = succeeded
        self.provider.queries = deque([succeeded, succeeded])
        self.downloader.error = RuntimeError("signed URL")
        with self.assertRaises(VideoEngineArtifactDownloadError):
            self.engine.generate(request(), Path("out.mp4"), POLICY)
        persisted = self.registry.load("task-01")
        self.assertEqual(persisted.normalized_status, GenerationTaskStatus.SUCCEEDED)
        self.assertIsNone(persisted.artifact)

    def test_resume_submitted_and_processing_never_submit(self) -> None:
        for initial in (GenerationTaskStatus.SUBMITTED, GenerationTaskStatus.PROCESSING):
            with self.subTest(initial=initial):
                self._reset()
                self.registry.create(record(initial))
                succeeded = task(GenerationTaskStatus.SUCCEEDED, [video()])
                self.provider.queries = deque([succeeded, succeeded])
                result = self.engine.resume("task-01", Path("out.mp4"), POLICY)
                self.assertEqual(result.normalized_status, GenerationTaskStatus.SUCCEEDED)
                self.assertEqual(self.provider.submit_calls, 0)
                self.assertEqual(self.downloader.calls, 1)

    def test_resume_succeeded_without_artifact_downloads(self) -> None:
        self.registry.create(record(GenerationTaskStatus.SUCCEEDED))
        self.provider.queries = deque([task(GenerationTaskStatus.SUCCEEDED, [video()])])
        result = self.engine.resume("task-01", Path("out.mp4"), POLICY)
        self.assertIsNotNone(result.artifact)
        self.assertEqual(self.provider.submit_calls, 0)
        self.assertEqual(self.downloader.calls, 1)

    def test_resume_succeeded_with_artifact_returns_without_provider_or_downloader(self) -> None:
        artifact = ArtifactRecord(
            artifact_id="video-01",
            local_path=Path("already.mp4"),
            byte_size=5,
            sha256="a" * 64,
            content_type="video/mp4",
        )
        self.registry.create(record(GenerationTaskStatus.SUCCEEDED, artifact))
        result = self.engine.resume("task-01", Path("ignored.mp4"), POLICY)
        self.assertEqual(result.artifact, artifact)
        self.assertEqual(self.provider.submit_calls, 0)
        self.assertEqual(self.provider.query_calls, 0)
        self.assertEqual(self.downloader.calls, 0)

    def test_resume_failed_and_missing_are_explicit_and_never_submit(self) -> None:
        self.registry.create(record(GenerationTaskStatus.FAILED))
        with self.assertRaises(VideoEngineTaskFailedError):
            self.engine.resume("task-01", Path("out.mp4"), POLICY)
        with self.assertRaises(VideoTaskNotFoundError):
            self.engine.resume("missing", Path("out.mp4"), POLICY)
        self.assertEqual(self.provider.submit_calls, 0)

    def _reset(self) -> None:
        self.registry = TaskRegistry(Path(self.temporary.name) / f"tasks-{id(object())}")
        self.provider = WorkflowProvider()
        self.downloader = WorkflowDownloader()
        self.clock = FakeClock()
        self.engine = VideoEngine({"fake": self.provider}, self.registry, self.downloader, default_provider="fake", monotonic_clock=self.clock.monotonic, sleeper=self.clock.sleep)


class WorkflowProvider:
    def __init__(self) -> None:
        self.submitted = task(GenerationTaskStatus.SUBMITTED)
        self.submit_error = None
        self.queries = deque()
        self.submit_calls = 0
        self.query_calls = 0

    def submit_generation(self, generation_request):
        self.submit_calls += 1
        if self.submit_error:
            raise self.submit_error
        return self.submitted

    def get_task_by_id(self, provider_task_id):
        self.query_calls += 1
        response = self.queries[0] if len(self.queries) == 1 else self.queries.popleft()
        if isinstance(response, Exception):
            raise response
        return response


class WorkflowDownloader:
    def __init__(self) -> None:
        self.calls = 0
        self.error = None

    def download_video_artifact(self, artifact, destination, *, overwrite=False):
        self.calls += 1
        if self.error:
            raise self.error
        return DownloadedVideoArtifact(artifact_id=artifact.artifact_id, local_path=destination, byte_size=5, sha256="a" * 64, content_type="video/mp4")


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.value

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.value += seconds


def task(status, artifacts=None):
    return GenerationTask(external_task_id="task-01", provider_name="fake", provider_status=status.value, normalized_status=status, external_correlation_id="correlation-01", artifacts=artifacts or [], updated_at=NOW)


def video():
    return VideoArtifact(artifact_id="video-01", url="https://cdn.test/video?signed-secret", content_type="video/mp4")


def record(status, artifact=None):
    return GenerationTaskRecord(provider="fake", provider_task_id="task-01", external_correlation_id="correlation-01", normalized_status=status, created_at=NOW, updated_at=NOW, artifact=artifact)
