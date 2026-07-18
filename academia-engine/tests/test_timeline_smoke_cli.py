import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.cli.video_timeline_smoke_test import (
    TimelineSmokeHashError,
    TimelineSmokeRuntime,
    TimelineSmokeTransitionError,
    build_smoke_timeline,
    main,
    verify_input_hashes,
)
from app.media import MediaProbeResult, MediaToolAvailabilityError
from app.timeline import (
    FFmpegTimelineRenderer,
    RenderedTimelineArtifact,
    TimelineMediaValidator,
    TimelineRenderExecutionError,
    TimelineTransitionKind,
)


class TimelineSmokeHarnessTests(unittest.TestCase):
    def test_deterministic_cut_fade_and_dissolve_timeline_construction(self) -> None:
        inputs = [Path("second-name.mp4"), Path("first-name.mp4"), Path("third.mp4")]
        cut = build_smoke_timeline(inputs, Path("work"), Path("out.mp4"))
        self.assertEqual([scene.scene_id for scene in cut.scenes], ["scene-0001", "scene-0002", "scene-0003"])
        self.assertEqual([scene.source_path for scene in cut.scenes], inputs)
        self.assertEqual([scene.order for scene in cut.scenes], [0, 1, 2])
        self.assertTrue(all(scene.transition_to_next.kind == TimelineTransitionKind.CUT for scene in cut.scenes[:-1]))
        self.assertIsNone(cut.scenes[-1].transition_to_next)
        for kind in ("fade", "dissolve"):
            value = build_smoke_timeline(inputs[:2], Path("work"), Path("out.mp4"), kind, 0.5)
            self.assertEqual(value.scenes[0].transition_to_next.kind.value, kind)
            self.assertEqual(value.scenes[0].transition_to_next.duration_seconds, 0.5)

    def test_invalid_transition_duration_is_explicit(self) -> None:
        inputs = [Path("one.mp4"), Path("two.mp4")]
        invalid = [("cut", 0.5), ("fade", None), ("fade", 0), ("dissolve", -1), ("dissolve", float("inf"))]
        for kind, duration in invalid:
            with self.subTest(kind=kind, duration=duration), self.assertRaises(TimelineSmokeTransitionError):
                build_smoke_timeline(inputs, Path("work"), Path("out.mp4"), kind, duration)

    def test_sha256_verification_success_mismatch_and_count(self) -> None:
        with TemporaryDirectory() as directory:
            first, second = Path(directory) / "one.mp4", Path(directory) / "two.mp4"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            hashes = [hashlib.sha256(b"one").hexdigest(), hashlib.sha256(b"two").hexdigest()]
            verify_input_hashes([first, second], hashes)
            with self.assertRaises(TimelineSmokeHashError):
                verify_input_hashes([first, second], [hashes[0], "0" * 64])
            with self.assertRaises(TimelineSmokeHashError):
                verify_input_hashes([first, second], [hashes[0]])

    def test_cli_production_sequence_preflight_and_success_are_sanitized(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = root / "one.mp4", root / "two.mp4"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            events = []
            runtime = fake_runtime(events, {first: probe_info(first), second: probe_info(second)})
            arguments = ["video_timeline_smoke_test", "--input", str(first), "--input", str(second), "--workspace", str(root / "work"), "--output", str(root / "out.mp4")]
            with patch("sys.argv", arguments), patch("app.cli.video_timeline_smoke_test.build_runtime", return_value=runtime), patch("app.cli.video_timeline_smoke_test.print") as output:
                self.assertEqual(main(), 0)
        self.assertEqual(events, ["tools", "validate", "render"])
        text = "\n".join(call.args[0] for call in output.call_args_list)
        self.assertIn("Inputs: 2", text)
        self.assertIn("Transition: cut", text)
        self.assertIn("Expected timeline duration: 10.0", text)
        self.assertIn("Scene: scene-0001", text)
        self.assertIn("Timeline ID: timeline-smoke-test", text)
        self.assertIn("Video codec: h264", text)
        for forbidden in ("filter_complex", "ffmpeg -", "raw", "Authorization", "signed", "billing"):
            self.assertNotIn(forbidden, text)

    def test_tool_unavailable_is_sanitized(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = root / "one.mp4", root / "two.mp4"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            runtime = fake_runtime([], {first: probe_info(first), second: probe_info(second)})
            runtime.tool_checker.error = MediaToolAvailabilityError("Required media tool is unavailable: ffmpeg")
            arguments = ["video_timeline_smoke_test", "--input", str(first), "--input", str(second), "--workspace", str(root / "work"), "--output", str(root / "out.mp4")]
            with patch("sys.argv", arguments), patch("app.cli.video_timeline_smoke_test.build_runtime", return_value=runtime), patch("app.cli.video_timeline_smoke_test.print") as output:
                self.assertEqual(main(), 1)
        self.assertEqual(output.call_args.args[0], "Timeline smoke test unavailable: Required media tool is unavailable: ffmpeg")

    def test_missing_input_stops_before_runtime_wiring(self) -> None:
        arguments = ["video_timeline_smoke_test", "--input", "missing-one.mp4", "--input", "missing-two.mp4", "--workspace", "work", "--output", "out.mp4"]
        with patch("sys.argv", arguments), patch("app.cli.video_timeline_smoke_test.build_runtime") as runtime, patch("app.cli.video_timeline_smoke_test.print") as output:
            self.assertEqual(main(), 1)
        runtime.assert_not_called()
        self.assertIn("input 1 is missing", output.call_args.args[0])

    def test_render_failure_output_has_no_traceback_or_internal_details(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = root / "one.mp4", root / "two.mp4"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            runtime = fake_runtime([], {first: probe_info(first), second: probe_info(second)})
            runtime.renderer.error = TimelineRenderExecutionError("ffmpeg timeline render failed with exit code 1.")
            arguments = ["video_timeline_smoke_test", "--input", str(first), "--input", str(second), "--workspace", str(root / "work"), "--output", str(root / "out.mp4")]
            with patch("sys.argv", arguments), patch("app.cli.video_timeline_smoke_test.build_runtime", return_value=runtime), patch("app.cli.video_timeline_smoke_test.print") as output:
                self.assertEqual(main(), 1)
        text = "\n".join(call.args[0] for call in output.call_args_list)
        self.assertIn("exit code 1", text)
        for forbidden in ("Traceback", "filter_complex", "Authorization", "signed-secret", ".part"):
            self.assertNotIn(forbidden, text)


class FakeToolChecker:
    def __init__(self, events):
        self.events = events
        self.error = None

    def require_available(self):
        self.events.append("tools")
        if self.error:
            raise self.error


class RecordingValidator:
    def __init__(self, events, responses):
        self.events = events
        self.real = TimelineMediaValidator(FakeProbe(responses))

    def validate(self, timeline):
        self.events.append("validate")
        return self.real.validate(timeline)


class RecordingRenderer:
    def __init__(self, events):
        self.events = events
        self.error = None

    def render(self, plan, overwrite=False):
        self.events.append("render")
        if self.error:
            raise self.error
        info = probe_info(plan.destination).model_copy(update={"duration_seconds": plan.expected_duration_seconds})
        return RenderedTimelineArtifact(timeline_id=plan.timeline_id, local_path=plan.destination, byte_size=5, sha256="a" * 64, media_info=info, source_count=len(plan.scenes), transition_count=len(plan.transitions))


class FakeProbe:
    def __init__(self, responses):
        self.responses = responses

    def probe_video(self, path):
        return self.responses[path]


def fake_runtime(events, responses):
    return TimelineSmokeRuntime(FakeToolChecker(events), RecordingValidator(events, responses), RecordingRenderer(events))


def probe_info(path):
    return MediaProbeResult(local_path=path, duration_seconds=5, width=1280, height=720, frame_rate=30, video_codec="h264", audio_codec="aac", has_audio=True, container_format="mp4")
