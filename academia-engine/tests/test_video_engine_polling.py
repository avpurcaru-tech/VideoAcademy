from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pydantic import ValidationError

from app.models import GenerationTask, GenerationTaskStatus, VideoArtifact
from app.services import (
    DownloadedVideoArtifact,
    GenerationTaskRecord,
    TaskRegistry,
    VideoEngine,
    VideoEngineAttemptsExceededError,
    VideoEngineTaskFailedError,
    VideoEngineTimeoutError,
    VideoPollingPolicy,
    VideoProviderOperationError,
)


NOW = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)


class VideoEnginePollingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.registry = TaskRegistry(Path(self.temporary.name) / "tasks")
        self.registry.create(record())
        self.clock = FakeClock()
        self.provider = SequenceProvider()
        self.downloader = FakeDownloader()
        self.engine = VideoEngine(
            {"fake": self.provider},
            self.registry,
            self.downloader,
            monotonic_clock=self.clock.monotonic,
            sleeper=self.clock.sleep,
        )
        self.policy = VideoPollingPolicy(interval_seconds=2, timeout_seconds=10)

    def test_policy_rejects_non_positive_values(self) -> None:
        for values in [
            {"interval_seconds": 0, "timeout_seconds": 1},
            {"interval_seconds": 1, "timeout_seconds": 0},
            {"interval_seconds": 1, "timeout_seconds": 1, "max_attempts": 0},
        ]:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                VideoPollingPolicy(**values)

    def test_immediate_succeeded_and_failed_never_sleep(self) -> None:
        for status in (GenerationTaskStatus.SUCCEEDED, GenerationTaskStatus.FAILED):
            with self.subTest(status=status):
                self.provider.responses = deque([task(status)])
                result = self.engine.wait_until_terminal("task-01", self.policy)
                self.assertEqual(result.normalized_status, status)
                self.assertEqual(self.clock.sleeps, [])

    def test_submitted_processing_succeeded_refreshes_three_times_and_sleeps_between(self) -> None:
        self.provider.responses = deque([
            task(GenerationTaskStatus.SUBMITTED),
            task(GenerationTaskStatus.PROCESSING),
            task(GenerationTaskStatus.SUCCEEDED),
        ])

        result = self.engine.wait_until_terminal("task-01", self.policy)

        self.assertEqual(result.normalized_status, GenerationTaskStatus.SUCCEEDED)
        self.assertEqual(self.provider.calls, 3)
        self.assertEqual(self.clock.sleeps, [2, 2])

    def test_submitted_then_failed_sleeps_once(self) -> None:
        self.provider.responses = deque([
            task(GenerationTaskStatus.SUBMITTED),
            task(GenerationTaskStatus.FAILED),
        ])
        result = self.engine.wait_until_terminal("task-01", self.policy)
        self.assertEqual(result.normalized_status, GenerationTaskStatus.FAILED)
        self.assertEqual(self.clock.sleeps, [2])

    def test_timeout_clamps_sleep_and_preserves_last_persisted_state(self) -> None:
        self.provider.responses = deque([task(GenerationTaskStatus.PROCESSING)])
        policy = VideoPollingPolicy(interval_seconds=10, timeout_seconds=3)

        with self.assertRaises(VideoEngineTimeoutError):
            self.engine.wait_until_terminal("task-01", policy)

        self.assertEqual(self.provider.calls, 1)
        self.assertEqual(self.clock.sleeps, [3])
        self.assertEqual(self.clock.value, 3)
        self.assertEqual(
            self.registry.load("task-01").normalized_status,
            GenerationTaskStatus.PROCESSING,
        )

    def test_max_attempts_is_distinct_and_counts_first_refresh(self) -> None:
        self.provider.responses = deque([task(GenerationTaskStatus.PROCESSING)])
        policy = VideoPollingPolicy(interval_seconds=2, timeout_seconds=10, max_attempts=1)
        with self.assertRaises(VideoEngineAttemptsExceededError):
            self.engine.wait_until_terminal("task-01", policy)
        self.assertEqual(self.provider.calls, 1)
        self.assertEqual(self.clock.sleeps, [])

    def test_provider_failure_stops_without_sleep_or_retry(self) -> None:
        self.provider.responses = deque([RuntimeError("Authorization: signed-secret")])
        with self.assertRaises(VideoProviderOperationError) as caught:
            self.engine.wait_until_terminal("task-01", self.policy)
        self.assertEqual(self.provider.calls, 1)
        self.assertEqual(self.clock.sleeps, [])
        self.assertNotIn("secret", str(caught.exception))

    def test_wait_and_download_succeeds_after_terminal_wait(self) -> None:
        succeeded = task(
            GenerationTaskStatus.SUCCEEDED,
            artifacts=[VideoArtifact(artifact_id="video-01", url="https://cdn.test/signed")],
        )
        self.provider.responses = deque([succeeded, succeeded])
        result = self.engine.wait_and_download("task-01", Path("out.mp4"), self.policy)
        self.assertEqual(self.provider.calls, 2)
        self.assertEqual(self.downloader.calls, 1)
        self.assertEqual(result.artifact.artifact_id, "video-01")

    def test_wait_and_download_does_not_download_failed_task(self) -> None:
        self.provider.responses = deque([task(GenerationTaskStatus.FAILED)])
        with self.assertRaises(VideoEngineTaskFailedError):
            self.engine.wait_and_download("task-01", Path("out.mp4"), self.policy)
        self.assertEqual(self.downloader.calls, 0)


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


class SequenceProvider:
    def __init__(self) -> None:
        self.responses = deque()
        self.calls = 0

    def get_task_by_id(self, provider_task_id):
        self.calls += 1
        response = self.responses[0] if len(self.responses) == 1 else self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response


class FakeDownloader:
    def __init__(self) -> None:
        self.calls = 0

    def download_video_artifact(self, artifact, destination, *, overwrite=False):
        self.calls += 1
        return DownloadedVideoArtifact(
            artifact_id=artifact.artifact_id,
            local_path=destination,
            byte_size=5,
            sha256="a" * 64,
            content_type="video/mp4",
        )


def record() -> GenerationTaskRecord:
    return GenerationTaskRecord(
        provider="fake",
        provider_task_id="task-01",
        external_correlation_id="external-01",
        normalized_status=GenerationTaskStatus.SUBMITTED,
        created_at=NOW,
        updated_at=NOW,
    )


def task(status, artifacts=None) -> GenerationTask:
    return GenerationTask(
        external_task_id="task-01",
        provider_name="fake",
        provider_status=status.value,
        normalized_status=status,
        artifacts=artifacts or [],
        updated_at=NOW,
    )
