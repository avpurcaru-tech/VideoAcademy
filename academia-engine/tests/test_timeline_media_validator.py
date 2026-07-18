from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.media import MediaProbeResult
from app.timeline import (
    TimelineEmptyEffectiveDurationError,
    TimelineMediaProbeError,
    TimelineMediaValidator,
    TimelineOutput,
    TimelineScene,
    TimelineSourceMissingError,
    TimelineSourceNotFileError,
    TimelineTransition,
    TimelineTransitionCurrentSceneError,
    TimelineTransitionNextSceneError,
    TimelineTrimEndOutOfBoundsError,
    TimelineTrimStartOutOfBoundsError,
    VideoTimeline,
)
from app.timeline.resolver import resolve_timeline


class TimelineMediaValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.one = self.root / "one.mp4"
        self.two = self.root / "two.mp4"
        self.three = self.root / "three.mp4"
        for path in (self.one, self.two, self.three):
            path.write_bytes(b"video")

    def test_valid_untrimmed_cut_timeline_resolves_once_and_preserves_metadata(self) -> None:
        timeline = self._timeline([scene("two", self.two, 5), scene("one", self.one, 1)])
        probe = FakeProbe({self.one: media(self.one, 10), self.two: media(self.two, 20, audio=False)})
        before = timeline.to_json()
        with patch("app.timeline.validator.resolve_timeline", wraps=resolve_timeline) as resolver:
            result = TimelineMediaValidator(probe).validate(timeline)
        resolver.assert_called_once_with(timeline)
        self.assertEqual([item.scene_id for item in result.scenes], ["one", "two"])
        self.assertEqual(result.total_duration_seconds, 30)
        self.assertEqual(result.scenes[0].effective_start_seconds, 0)
        self.assertEqual(result.scenes[0].effective_end_seconds, 10)
        self.assertEqual(result.scenes[1].source_media_info, probe.responses[self.two])
        self.assertFalse(result.scenes[1].source_media_info.has_audio)
        self.assertEqual(timeline.to_json(), before)

    def test_valid_full_start_only_and_end_only_trim_semantics(self) -> None:
        cases = [
            (scene("one", self.one, 0, start=2, end=7), (2, 7, 5)),
            (scene("one", self.one, 0, start=3), (3, 10, 7)),
            (scene("one", self.one, 0, end=6), (0, 6, 6)),
        ]
        for first, expected in cases:
            with self.subTest(expected=expected):
                result = TimelineMediaValidator(FakeProbe({self.one: media(self.one, 10), self.two: media(self.two, 5)})).validate(self._timeline([first, scene("two", self.two, 1)]))
                actual = result.scenes[0]
                self.assertEqual((actual.effective_start_seconds, actual.effective_end_seconds, actual.effective_duration_seconds), expected)

    def test_duplicate_source_is_probed_once_but_scenes_are_preserved(self) -> None:
        probe = FakeProbe({self.one: media(self.one, 10)})
        result = TimelineMediaValidator(probe).validate(self._timeline([scene("repeat-one", self.one, 0), scene("repeat-two", self.one, 1)]))
        self.assertEqual(probe.calls, [self.one])
        self.assertEqual([item.scene_id for item in result.scenes], ["repeat-one", "repeat-two"])
        self.assertIs(result.scenes[0].source_media_info, result.scenes[1].source_media_info)

    def test_missing_and_directory_sources_are_distinct_errors(self) -> None:
        directory = self.root / "directory"
        directory.mkdir()
        cases = [(self.root / "missing.mp4", TimelineSourceMissingError), (directory, TimelineSourceNotFileError)]
        for source, error_type in cases:
            with self.subTest(error=error_type.__name__), self.assertRaises(error_type):
                TimelineMediaValidator(FakeProbe({})).validate(self._timeline([scene("bad", source, 0), scene("good", self.one, 1)]))

    def test_probe_failure_is_wrapped_without_raw_details(self) -> None:
        probe = FakeProbe({self.one: RuntimeError("raw ffprobe json Authorization secret"), self.two: media(self.two, 10)})
        with self.assertRaises(TimelineMediaProbeError) as caught:
            TimelineMediaValidator(probe).validate(self._timeline([scene("one", self.one, 0), scene("two", self.two, 1)]))
        self.assertNotIn("secret", str(caught.exception))
        self.assertIsInstance(caught.exception.__cause__, RuntimeError)

    def test_invalid_source_duration_is_explicit(self) -> None:
        from app.timeline import TimelineInvalidSourceDurationError
        invalid = SimpleNamespace(duration_seconds=0, width=1280, height=720, frame_rate=30)
        with self.assertRaises(TimelineInvalidSourceDurationError):
            TimelineMediaValidator(FakeProbe({self.one: invalid, self.two: media(self.two, 10)})).validate(self._timeline([scene("one", self.one, 0), scene("two", self.two, 1)]))

    def test_trim_start_equal_beyond_end_beyond_and_empty_duration_are_rejected(self) -> None:
        cases = [
            (scene("one", self.one, 0, start=10), TimelineTrimStartOutOfBoundsError),
            (scene("one", self.one, 0, start=11), TimelineTrimStartOutOfBoundsError),
            (scene("one", self.one, 0, end=11), TimelineTrimEndOutOfBoundsError),
            (scene("one", self.one, 0, start=9.9999995), TimelineEmptyEffectiveDurationError),
        ]
        for first, error_type in cases:
            with self.subTest(error=error_type.__name__), self.assertRaises(error_type):
                TimelineMediaValidator(FakeProbe({self.one: media(self.one, 10), self.two: media(self.two, 10)})).validate(self._timeline([first, scene("two", self.two, 1)]))

    def test_fade_and_dissolve_subtract_overlap_from_total(self) -> None:
        for kind in ("fade", "dissolve"):
            with self.subTest(kind=kind):
                transition = TimelineTransition(kind=kind, duration_seconds=2)
                value = self._timeline([scene("one", self.one, 0, transition=transition), scene("two", self.two, 1)])
                result = TimelineMediaValidator(FakeProbe({self.one: media(self.one, 10), self.two: media(self.two, 8)})).validate(value)
                self.assertEqual(result.total_duration_seconds, 16)

    def test_transition_equal_or_greater_than_current_scene_is_rejected(self) -> None:
        for duration in (5, 6):
            with self.subTest(duration=duration), self.assertRaises(TimelineTransitionCurrentSceneError):
                value = self._timeline([scene("one", self.one, 0, end=5, transition=TimelineTransition(kind="fade", duration_seconds=duration)), scene("two", self.two, 1)])
                TimelineMediaValidator(FakeProbe({self.one: media(self.one, 10), self.two: media(self.two, 10)})).validate(value)

    def test_transition_equal_or_greater_than_next_scene_is_rejected(self) -> None:
        for duration in (3, 4):
            with self.subTest(duration=duration), self.assertRaises(TimelineTransitionNextSceneError):
                value = self._timeline([scene("one", self.one, 0, transition=TimelineTransition(kind="dissolve", duration_seconds=duration)), scene("two", self.two, 1, end=3)])
                TimelineMediaValidator(FakeProbe({self.one: media(self.one, 10), self.two: media(self.two, 10)})).validate(value)

    def test_multiple_overlaps_have_deterministic_total(self) -> None:
        value = self._timeline([
            scene("one", self.one, 0, transition=TimelineTransition(kind="fade", duration_seconds=1)),
            scene("two", self.two, 1, transition=TimelineTransition(kind="dissolve", duration_seconds=2)),
            scene("three", self.three, 2),
        ])
        result = TimelineMediaValidator(FakeProbe({self.one: media(self.one, 10), self.two: media(self.two, 8), self.three: media(self.three, 6)})).validate(value)
        self.assertEqual(result.total_duration_seconds, 21)

    def _timeline(self, scenes):
        return VideoTimeline(timeline_id="timeline-01", scenes=tuple(scenes), output=TimelineOutput(destination=self.root / "final.mp4", workspace=self.root / "work"))


class FakeProbe:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def probe_video(self, path):
        self.calls.append(path)
        response = self.responses[path]
        if isinstance(response, Exception):
            raise response
        return response


def media(path, duration, audio=True):
    return MediaProbeResult(local_path=path, duration_seconds=duration, width=1280, height=720, frame_rate=30, video_codec="h264", audio_codec="aac" if audio else None, has_audio=audio, container_format="mp4")


def scene(scene_id, path, order, start=None, end=None, transition=None):
    return TimelineScene(scene_id=scene_id, source_path=path, order=order, trim_start_seconds=start, trim_end_seconds=end, transition_to_next=transition)
