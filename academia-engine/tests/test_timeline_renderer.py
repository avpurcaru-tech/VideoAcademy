import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.media import MediaProbeResult, ProcessResult
from app.timeline import (
    FFmpegTimelineRenderer,
    TimelineRenderAudioMismatchError,
    TimelineRenderCodecMismatchError,
    TimelineRenderDestinationConflictError,
    TimelineRenderDurationMismatchError,
    TimelineRenderExecutionError,
    TimelineRenderFrameRateMismatchError,
    TimelineRenderedOutputEmptyError,
    TimelineRenderedOutputMissingError,
    TimelineRenderProbeError,
    TimelineRenderResolutionMismatchError,
    TimelineTransition,
    build_render_plan,
)
from app.timeline.ffmpeg_compiler import compile_ffmpeg_timeline
from tests.test_timeline_render_plan import validated, vscene


CONTENT = b"rendered-timeline"


class TimelineRendererTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_success_compiles_to_temporary_executes_once_probes_once_and_publishes_atomically(self) -> None:
        plan = self._plan(audio=True)
        runner = RenderRunner()
        probe = FakeProbe(media(self.root / "temporary.mp4", duration=9, audio=True))
        original_replace = os.replace
        replacements = []
        with patch("app.timeline.renderer.compile_ffmpeg_timeline", wraps=compile_ffmpeg_timeline) as compiler, patch("app.timeline.renderer.os.replace", side_effect=lambda source, destination: (replacements.append((Path(source), Path(destination))), original_replace(source, destination))[1]):
            artifact = FFmpegTimelineRenderer(runner, probe).render(plan)

        self.assertEqual(len(runner.calls), 1)
        self.assertIsInstance(runner.calls[0][0], tuple)
        self.assertEqual(runner.calls[0][1], None)
        self.assertEqual(len(probe.calls), 1)
        compiler.assert_called_once()
        temporary = compiler.call_args.kwargs["output_path"]
        self.assertNotEqual(temporary, plan.destination)
        self.assertEqual(temporary.parent, plan.destination.parent)
        self.assertEqual(compiler.call_args.kwargs["overwrite"], False)
        self.assertEqual(runner.calls[0][0][-1], str(temporary))
        self.assertEqual(replacements, [(temporary, plan.destination)])
        self.assertEqual(artifact.sha256, hashlib.sha256(CONTENT).hexdigest())
        self.assertEqual(artifact.byte_size, len(CONTENT))
        self.assertEqual(artifact.local_path, plan.destination)
        self.assertEqual(artifact.media_info.local_path, plan.destination)
        self.assertEqual(artifact.source_count, 2)
        self.assertEqual(artifact.transition_count, 1)
        self.assertNotIn("part", str(artifact.model_dump()))
        self.assertEqual(plan.destination.read_bytes(), CONTENT)

    def test_destination_conflict_happens_before_compilation_or_execution(self) -> None:
        plan = self._plan()
        plan.destination.write_bytes(b"existing")
        runner = RenderRunner()
        with patch("app.timeline.renderer.compile_ffmpeg_timeline") as compiler, self.assertRaises(TimelineRenderDestinationConflictError):
            FFmpegTimelineRenderer(runner, FakeProbe(media(plan.destination))).render(plan)
        compiler.assert_not_called()
        self.assertEqual(runner.calls, [])
        self.assertEqual(plan.destination.read_bytes(), b"existing")

    def test_overwrite_preserves_existing_destination_until_success(self) -> None:
        plan = self._plan()
        plan.destination.write_bytes(b"existing")
        runner = RenderRunner(expected_existing=(plan.destination, b"existing"))
        artifact = FFmpegTimelineRenderer(runner, FakeProbe(media(plan.destination, duration=9))).render(plan, overwrite=True)
        self.assertEqual(runner.calls[0][0][1], "-y")
        self.assertEqual(plan.destination.read_bytes(), CONTENT)
        self.assertEqual(artifact.local_path, plan.destination)

    def test_nonzero_exit_is_safe_and_cleans_temporary_while_preserving_destination(self) -> None:
        plan = self._plan()
        plan.destination.write_bytes(b"existing")
        runner = RenderRunner(exit_code=7, stderr="Authorization: Bearer secret-token")
        with self.assertRaises(TimelineRenderExecutionError) as caught:
            FFmpegTimelineRenderer(runner, FakeProbe(media(plan.destination))).render(plan, overwrite=True)
        self.assertIn("exit code 7", str(caught.exception))
        self.assertNotIn("secret-token", str(caught.exception))
        self.assertEqual(plan.destination.read_bytes(), b"existing")
        self.assertEqual(self._temporary_files(), [])

    def test_missing_and_empty_outputs_are_explicit_and_cleaned(self) -> None:
        cases = [(RenderRunner(write_output=False), TimelineRenderedOutputMissingError), (RenderRunner(content=b""), TimelineRenderedOutputEmptyError)]
        for runner, error_type in cases:
            with self.subTest(error=error_type.__name__), self.assertRaises(error_type):
                FFmpegTimelineRenderer(runner, FakeProbe(media(Path("x")))).render(self._plan())
            self.assertEqual(self._temporary_files(), [])

    def test_probe_failure_is_wrapped_and_temporary_is_removed(self) -> None:
        with self.assertRaises(TimelineRenderProbeError) as caught:
            FFmpegTimelineRenderer(RenderRunner(), FakeProbe(RuntimeError("raw ffprobe Authorization secret"))).render(self._plan())
        self.assertNotIn("secret", str(caught.exception))
        self.assertEqual(self._temporary_files(), [])

    def test_resolution_frame_rate_codec_and_duration_mismatches_are_rejected(self) -> None:
        cases = [
            (media(Path("x"), width=640), TimelineRenderResolutionMismatchError),
            (media(Path("x"), frame_rate=24), TimelineRenderFrameRateMismatchError),
            (media(Path("x"), codec="hevc"), TimelineRenderCodecMismatchError),
            (media(Path("x"), duration=9.251), TimelineRenderDurationMismatchError),
        ]
        for result, error_type in cases:
            with self.subTest(error=error_type.__name__), self.assertRaises(error_type):
                FFmpegTimelineRenderer(RenderRunner(), FakeProbe(result)).render(self._plan())
            self.assertFalse((self.root / "final.mp4").exists())
            self.assertEqual(self._temporary_files(), [])

    def test_audio_presence_must_exactly_match_compiled_command(self) -> None:
        cases = [(self._plan(audio=True), False), (self._plan(audio=False), True)]
        for plan, output_audio in cases:
            with self.subTest(expected=plan.scenes[0].has_audio), self.assertRaises(TimelineRenderAudioMismatchError):
                FFmpegTimelineRenderer(RenderRunner(), FakeProbe(media(Path("x"), duration=9, audio=output_audio))).render(plan)
            self.assertEqual(self._temporary_files(), [])

    def test_no_audio_command_accepts_no_audio_and_duration_at_tolerance(self) -> None:
        plan = self._plan(audio=False)
        artifact = FFmpegTimelineRenderer(RenderRunner(), FakeProbe(media(Path("x"), duration=9.25, audio=False))).render(plan)
        self.assertFalse(artifact.media_info.has_audio)

    def test_plan_is_not_mutated(self) -> None:
        plan = self._plan()
        before = plan.to_json()
        FFmpegTimelineRenderer(RenderRunner(), FakeProbe(media(Path("x"), duration=9))).render(plan)
        self.assertEqual(plan.to_json(), before)

    def _plan(self, audio=True):
        value = validated([vscene("one", 0, 0, 5, audio=audio, transition=TimelineTransition(kind="fade", duration_seconds=1)), vscene("two", 1, 0, 5, audio=audio)], total=9)
        return build_render_plan(value).model_copy(update={"destination": self.root / "final.mp4"})

    def _temporary_files(self):
        return list(self.root.glob("*.part*"))


class RenderRunner:
    def __init__(self, content=CONTENT, exit_code=0, stderr="", write_output=True, expected_existing=None):
        self.content = content
        self.exit_code = exit_code
        self.stderr = stderr
        self.write_output = write_output
        self.expected_existing = expected_existing
        self.calls = []

    def run(self, args, timeout_seconds=None):
        self.calls.append((args, timeout_seconds))
        if self.expected_existing:
            path, expected = self.expected_existing
            if path.read_bytes() != expected:
                raise AssertionError("Existing destination changed before FFmpeg execution.")
        if self.write_output:
            Path(args[-1]).write_bytes(self.content)
        return ProcessResult(exit_code=self.exit_code, stdout="", stderr=self.stderr)


class FakeProbe:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def probe_video(self, path):
        self.calls.append(path)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response.model_copy(update={"local_path": path})


def media(path, duration=9, width=1280, frame_rate=30, codec="h264", audio=True):
    return MediaProbeResult(local_path=path, duration_seconds=duration, width=width, height=720, frame_rate=frame_rate, video_codec=codec, audio_codec="aac" if audio else None, has_audio=audio, container_format="mp4")
