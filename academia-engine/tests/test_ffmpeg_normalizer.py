import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.media import (
    FFmpegExecutionError,
    FFmpegVideoNormalizer,
    FFprobeAdapter,
    NormalizedVideoValidationError,
    ProcessResult,
    VideoNormalizationDestinationExistsError,
    VideoNormalizationProfile,
    VideoSourceNotFoundError,
)


PROFILE = VideoNormalizationProfile.academia_default()
CONTENT = b"normalized-video"


class FFmpegVideoNormalizerTests(unittest.TestCase):
    def test_success_uses_exact_argv_hashes_and_atomically_publishes(self) -> None:
        runner = MediaToolRunner()
        original_replace = os.replace
        replacements = []
        with TemporaryDirectory() as directory, patch(
            "app.media.ffmpeg.os.replace",
            side_effect=lambda source, destination: (replacements.append((Path(source), Path(destination))), original_replace(source, destination))[1],
        ):
            source = Path(directory) / "source with spaces.mp4"
            source.write_bytes(b"source")
            destination = Path(directory) / "nested" / "final.mp4"
            artifact = FFmpegVideoNormalizer(runner, FFprobeAdapter(runner)).normalize_video(source, destination, PROFILE)

            self.assertEqual(destination.read_bytes(), CONTENT)
            self.assertEqual(artifact.byte_size, len(CONTENT))
            self.assertEqual(artifact.sha256, hashlib.sha256(CONTENT).hexdigest())
            self.assertEqual(artifact.media_info.local_path, destination)
            self.assertEqual(len(replacements), 1)
            self.assertEqual(replacements[0][1], destination)
            self.assertEqual(list(destination.parent.glob("*.part*")), [])

        ffmpeg_args = runner.calls[0][0]
        self.assertEqual([call[0][0] for call in runner.calls].count("ffmpeg"), 1)
        self.assertEqual(ffmpeg_args[:7], ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source)])
        self.assertIn("scale=1280:720", ffmpeg_args)
        self.assertIn("0:a:0?", ffmpeg_args)
        self.assertEqual(ffmpeg_args[-2], "+faststart")
        self.assertTrue(ffmpeg_args[-1].endswith(".part.mp4"))
        self.assertIsInstance(ffmpeg_args, list)

    def test_ffmpeg_failure_cleans_temporary_output(self) -> None:
        runner = MediaToolRunner(ffmpeg_result=ProcessResult(exit_code=2, stdout="", stderr="failure"))
        with TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.write_bytes(b"source")
            destination = Path(directory) / "out.mp4"
            with self.assertRaises(FFmpegExecutionError):
                FFmpegVideoNormalizer(runner, FFprobeAdapter(runner)).normalize_video(source, destination, PROFILE)
            self.assertFalse(destination.exists())
            self.assertEqual(list(Path(directory).glob("*.part*")), [])

    def test_empty_output_is_rejected_and_cleaned(self) -> None:
        runner = MediaToolRunner(content=b"")
        with TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.write_bytes(b"source")
            with self.assertRaises(NormalizedVideoValidationError):
                FFmpegVideoNormalizer(runner, FFprobeAdapter(runner)).normalize_video(source, Path(directory) / "out.mp4", PROFILE)
            self.assertEqual(list(Path(directory).glob("*.part*")), [])

    def test_existing_destination_is_not_overwritten_or_executed(self) -> None:
        runner = MediaToolRunner()
        with TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            destination = Path(directory) / "out.mp4"
            source.write_bytes(b"source")
            destination.write_bytes(b"existing")
            with self.assertRaises(VideoNormalizationDestinationExistsError):
                FFmpegVideoNormalizer(runner, FFprobeAdapter(runner)).normalize_video(source, destination, PROFILE)
            self.assertEqual(destination.read_bytes(), b"existing")
            self.assertEqual(runner.calls, [])

    def test_final_dimensions_and_frame_rate_are_validated(self) -> None:
        for width, rate, message in [(640, 30, "dimensions"), (1280, 24, "frame rate")]:
            with self.subTest(message=message), TemporaryDirectory() as directory:
                runner = MediaToolRunner(width=width, frame_rate=rate)
                source = Path(directory) / "source.mp4"
                destination = Path(directory) / "out.mp4"
                source.write_bytes(b"source")
                with self.assertRaisesRegex(NormalizedVideoValidationError, message):
                    FFmpegVideoNormalizer(runner, FFprobeAdapter(runner)).normalize_video(source, destination, PROFILE)
                self.assertFalse(destination.exists())
                self.assertEqual(list(Path(directory).glob("*.part*")), [])

    def test_missing_source_stops_before_execution(self) -> None:
        runner = MediaToolRunner()
        with TemporaryDirectory() as directory, self.assertRaises(VideoSourceNotFoundError):
            FFmpegVideoNormalizer(runner, FFprobeAdapter(runner)).normalize_video(Path(directory) / "missing.mp4", Path(directory) / "out.mp4", PROFILE)
        self.assertEqual(runner.calls, [])


class MediaToolRunner:
    def __init__(self, *, content=CONTENT, width=1280, frame_rate=30, ffmpeg_result=None):
        self.content = content
        self.width = width
        self.frame_rate = frame_rate
        self.ffmpeg_result = ffmpeg_result or ProcessResult(exit_code=0, stdout="", stderr="")
        self.calls = []

    def run(self, args, timeout_seconds=None):
        args = list(args)
        self.calls.append((args, timeout_seconds))
        if args[0] == "ffmpeg":
            Path(args[-1]).write_bytes(self.content)
            return self.ffmpeg_result
        data = {
            "streams": [{"codec_type": "video", "codec_name": "h264", "width": self.width, "height": 720, "avg_frame_rate": f"{self.frame_rate}/1"}],
            "format": {"duration": "10", "format_name": "mp4"},
        }
        return ProcessResult(exit_code=0, stdout=json.dumps(data), stderr="")
