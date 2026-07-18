import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.media import (
    ConcatenatedDurationMismatchError,
    ConcatenatedMediaMismatchError,
    ConcatenationDestinationExistsError,
    ConcatenationSourceNotFoundError,
    ConcatManifestError,
    EmptyConcatenatedOutputError,
    FFmpegConcatError,
    FFmpegVideoConcatenator,
    IncompatibleVideoDimensionsError,
    IncompatibleVideoFrameRateError,
    InsufficientVideoSourcesError,
    MediaProbeResult,
    MixedAudioPresenceError,
    ProcessResult,
)


CONTENT = b"joined-scenes"


class VideoConcatenationTests(unittest.TestCase):
    def test_successful_two_file_concatenation_uses_duration_sum(self) -> None:
        with TemporaryDirectory() as directory:
            root, sources = make_sources(directory)
            runner = ConcatRunner()
            probe = FakeProbe(
                {sources[0]: media(sources[0], 1.25), sources[1]: media(sources[1], 2.75)},
                output_duration=4,
            )
            artifact = FFmpegVideoConcatenator(runner, probe).concatenate_videos(
                sources, root / "out.mp4"
            )
            self.assertEqual(artifact.source_count, 2)
            self.assertEqual(artifact.media_info.duration_seconds, 4)

    def test_success_preserves_order_escapes_paths_and_publishes_atomically(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            sources = [root / "scene one.mp4", root / "scenă două.mp4", root / "director's cut.mp4"]
            for source in sources:
                source.write_bytes(b"scene")
            runner = ConcatRunner()
            probe = FakeProbe({source: media(source, duration=2) for source in sources}, output_duration=6)
            destination = root / "final.mp4"
            original_replace = os.replace
            replacements = []
            with patch("app.media.concat.os.replace", side_effect=lambda source, dest: (replacements.append((Path(source), Path(dest))), original_replace(source, dest))[1]):
                artifact = FFmpegVideoConcatenator(runner, probe).concatenate_videos(sources, destination)

            self.assertEqual(artifact.source_count, 3)
            self.assertEqual(artifact.sha256, hashlib.sha256(CONTENT).hexdigest())
            self.assertEqual(destination.read_bytes(), CONTENT)
            self.assertEqual(len(replacements), 1)
            self.assertEqual(runner.ffmpeg_calls, 1)
            self.assertEqual(probe.calls[:3], sources)
            lines = runner.manifest.splitlines()
            self.assertEqual(len(lines), 3)
            self.assertIn("scene one.mp4", lines[0])
            self.assertIn("scenă două.mp4", lines[1])
            self.assertIn("director'\\''s cut.mp4", lines[2])
            self.assertEqual(list(root.glob(".concat.*")), [])
            self.assertEqual(list(root.glob("*.part*")), [])

            args = runner.calls[0]
            self.assertEqual(args[:10], ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i"])
            self.assertEqual(args[-3:-1], ["-c", "copy"])
            self.assertIsInstance(args, list)

    def test_zero_one_and_missing_sources_are_explicit(self) -> None:
        service = FFmpegVideoConcatenator(ConcatRunner(), FakeProbe({}))
        with self.assertRaises(InsufficientVideoSourcesError):
            service.concatenate_videos([], Path("out.mp4"))
        with self.assertRaises(InsufficientVideoSourcesError):
            service.concatenate_videos([Path("one.mp4")], Path("out.mp4"))
        with self.assertRaises(ConcatenationSourceNotFoundError):
            service.concatenate_videos([Path("one.mp4"), Path("two.mp4")], Path("out.mp4"))

    def test_incompatible_dimensions_frame_rates_and_audio_are_rejected_before_ffmpeg(self) -> None:
        cases = [
            ({"width": 640}, IncompatibleVideoDimensionsError),
            ({"frame_rate": 24}, IncompatibleVideoFrameRateError),
            ({"has_audio": False, "audio_codec": None}, MixedAudioPresenceError),
        ]
        for changes, error_type in cases:
            with self.subTest(error=error_type.__name__), TemporaryDirectory() as directory:
                root = Path(directory)
                first, second = root / "one.mp4", root / "two.mp4"
                first.write_bytes(b"1")
                second.write_bytes(b"2")
                runner = ConcatRunner()
                altered = media(second).model_copy(update=changes)
                with self.assertRaises(error_type):
                    FFmpegVideoConcatenator(runner, FakeProbe({first: media(first), second: altered})).concatenate_videos([first, second], root / "out.mp4")
                self.assertEqual(runner.ffmpeg_calls, 0)

    def test_ffmpeg_failure_and_empty_output_clean_manifest_and_temporary(self) -> None:
        for runner, error_type in [
            (ConcatRunner(exit_code=3), FFmpegConcatError),
            (ConcatRunner(content=b""), EmptyConcatenatedOutputError),
        ]:
            with self.subTest(error=error_type.__name__), TemporaryDirectory() as directory:
                root, sources = make_sources(directory)
                with self.assertRaises(error_type):
                    FFmpegVideoConcatenator(runner, FakeProbe({source: media(source) for source in sources})).concatenate_videos(sources, root / "out.mp4")
                self.assertEqual(list(root.glob(".concat.*")), [])
                self.assertEqual(list(root.glob("*.part*")), [])
                self.assertFalse((root / "out.mp4").exists())

    def test_duration_resolution_and_frame_rate_output_validation(self) -> None:
        cases = [
            ({"output_duration": 9}, ConcatenatedDurationMismatchError),
            ({"output_width": 640}, ConcatenatedMediaMismatchError),
            ({"output_rate": 24}, ConcatenatedMediaMismatchError),
        ]
        for options, error_type in cases:
            with self.subTest(error=error_type.__name__), TemporaryDirectory() as directory:
                root, sources = make_sources(directory)
                probe = FakeProbe({source: media(source) for source in sources}, **options)
                with self.assertRaises(error_type):
                    FFmpegVideoConcatenator(ConcatRunner(), probe).concatenate_videos(sources, root / "out.mp4")
                self.assertFalse((root / "out.mp4").exists())

    def test_existing_destination_is_rejected_without_tool_calls(self) -> None:
        with TemporaryDirectory() as directory:
            root, sources = make_sources(directory)
            destination = root / "out.mp4"
            destination.write_bytes(b"existing")
            runner = ConcatRunner()
            probe = FakeProbe({})
            with self.assertRaises(ConcatenationDestinationExistsError):
                FFmpegVideoConcatenator(runner, probe).concatenate_videos(sources, destination)
            self.assertEqual(probe.calls, [])
            self.assertEqual(runner.calls, [])

    def test_manifest_creation_failure_is_explicit(self) -> None:
        with TemporaryDirectory() as directory:
            root, sources = make_sources(directory)
            service = FFmpegVideoConcatenator(
                ConcatRunner(), FakeProbe({source: media(source) for source in sources})
            )
            with patch("app.media.concat.NamedTemporaryFile", side_effect=OSError("denied")):
                with self.assertRaises(ConcatManifestError):
                    service.concatenate_videos(sources, root / "out.mp4")


class ConcatRunner:
    def __init__(self, content=CONTENT, exit_code=0):
        self.content = content
        self.exit_code = exit_code
        self.calls = []
        self.manifest = ""
        self.ffmpeg_calls = 0

    def run(self, args, timeout_seconds=None):
        args = list(args)
        self.calls.append(args)
        self.ffmpeg_calls += 1
        self.manifest = Path(args[args.index("-i") + 1]).read_text(encoding="utf-8")
        Path(args[-1]).write_bytes(self.content)
        return ProcessResult(exit_code=self.exit_code, stdout="", stderr="safe failure")


class FakeProbe:
    def __init__(self, source_media, output_duration=4, output_width=1280, output_rate=30):
        self.source_media = source_media
        self.output_duration = output_duration
        self.output_width = output_width
        self.output_rate = output_rate
        self.calls = []

    def probe_video(self, path):
        path = Path(path)
        self.calls.append(path)
        if path in self.source_media:
            return self.source_media[path]
        return media(path, duration=self.output_duration).model_copy(update={"width": self.output_width, "frame_rate": self.output_rate})


def media(path, duration=2):
    return MediaProbeResult(local_path=path, duration_seconds=duration, width=1280, height=720, frame_rate=30, video_codec="h264", audio_codec="aac", has_audio=True, container_format="mp4")


def make_sources(directory):
    root = Path(directory)
    sources = [root / "one.mp4", root / "two.mp4"]
    for source in sources:
        source.write_bytes(b"scene")
    return root, sources
