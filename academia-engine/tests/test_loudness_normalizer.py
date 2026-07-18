import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.media import (
    AudioLoudnessProfile,
    EmptyLoudnessOutputError,
    FFmpegLoudnessNormalizer,
    LoudnessAnalysisError,
    LoudnessDestinationExistsError,
    LoudnessDurationMismatchError,
    LoudnessFFmpegError,
    LoudnessMediaMismatchError,
    LoudnessSourceNotFoundError,
    MalformedLoudnessAnalysisError,
    MediaAudioMissingError,
    MediaProbeResult,
    ProcessResult,
)


PROFILE = AudioLoudnessProfile.academia_default()
CONTENT = b"loudness-normalized"
MEASUREMENTS = {"input_i": "-22.1", "input_tp": "-3.2", "input_lra": "8.4", "input_thresh": "-32.0", "target_offset": "0.2"}


class LoudnessNormalizerTests(unittest.TestCase):
    def test_successful_two_pass_normalization_is_atomic_and_exact(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, destination = root / "input video.mp4", root / "output.mp4"
            source.write_bytes(b"source")
            runner = LoudnessRunner()
            probe = FakeProbe(source)
            original_replace = os.replace
            replacements = []
            with patch("app.media.loudness.os.replace", side_effect=lambda src, dst: (replacements.append((Path(src), Path(dst))), original_replace(src, dst))[1]):
                artifact = FFmpegLoudnessNormalizer(runner, probe).normalize_loudness(source, destination, PROFILE)

            self.assertEqual(len(runner.calls), 2)
            first, second = runner.calls
            self.assertEqual(first, ["ffmpeg", "-hide_banner", "-nostats", "-i", str(source), "-map", "0:a:0", "-af", "loudnorm=I=-16:LRA=11:TP=-1.5:print_format=json", "-f", "null", "-"])
            self.assertEqual(second[:7], ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source)])
            self.assertIn("-c:v", second)
            self.assertEqual(second[second.index("-c:v") + 1], "copy")
            loudnorm = second[second.index("-af") + 1]
            for expected in ("measured_I=-22.1", "measured_TP=-3.2", "measured_LRA=8.4", "measured_thresh=-32", "offset=0.2", "linear=true"):
                self.assertIn(expected, loudnorm)
            self.assertEqual(replacements[0][1], destination)
            self.assertEqual(artifact.sha256, hashlib.sha256(CONTENT).hexdigest())
            self.assertEqual(destination.read_bytes(), CONTENT)
            self.assertEqual(list(root.glob("*.part*")), [])

    def test_missing_source_and_audio_stop_before_ffmpeg(self) -> None:
        runner = LoudnessRunner()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(LoudnessSourceNotFoundError):
                FFmpegLoudnessNormalizer(runner, FakeProbe(root / "missing")).normalize_loudness(root / "missing", root / "out", PROFILE)
            source = root / "silent.mp4"
            source.write_bytes(b"source")
            with self.assertRaises(MediaAudioMissingError):
                FFmpegLoudnessNormalizer(runner, FakeProbe(source, source_audio=False)).normalize_loudness(source, root / "out", PROFILE)
        self.assertEqual(runner.calls, [])

    def test_analysis_failure_and_malformed_measurements_stop_before_pass_two(self) -> None:
        cases = [
            (LoudnessRunner(analysis_exit=2), LoudnessAnalysisError),
            (LoudnessRunner(analysis_text="not json"), MalformedLoudnessAnalysisError),
            (LoudnessRunner(measurements={"input_i": "-20"}), MalformedLoudnessAnalysisError),
            (LoudnessRunner(measurements={**MEASUREMENTS, "input_tp": "bad"}), MalformedLoudnessAnalysisError),
            (LoudnessRunner(measurements={**MEASUREMENTS, "input_tp": "NaN"}), MalformedLoudnessAnalysisError),
            (LoudnessRunner(measurements={**MEASUREMENTS, "input_tp": "Infinity"}), MalformedLoudnessAnalysisError),
        ]
        for runner, error_type in cases:
            with self.subTest(error=error_type.__name__), TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "source.mp4"
                source.write_bytes(b"source")
                with self.assertRaises(error_type):
                    FFmpegLoudnessNormalizer(runner, FakeProbe(source)).normalize_loudness(source, root / "out.mp4", PROFILE)
                self.assertEqual(len(runner.calls), 1)

    def test_second_pass_failure_and_empty_output_cleanup(self) -> None:
        for runner, error_type in [(LoudnessRunner(normalize_exit=3), LoudnessFFmpegError), (LoudnessRunner(content=b""), EmptyLoudnessOutputError)]:
            with self.subTest(error=error_type.__name__), TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "source.mp4"
                source.write_bytes(b"source")
                with self.assertRaises(error_type):
                    FFmpegLoudnessNormalizer(runner, FakeProbe(source)).normalize_loudness(source, root / "out.mp4", PROFILE)
                self.assertFalse((root / "out.mp4").exists())
                self.assertEqual(list(root.glob("*.part*")), [])

    def test_output_audio_resolution_frame_rate_and_duration_are_validated(self) -> None:
        cases = [
            ({"output_audio": False}, MediaAudioMissingError),
            ({"output_width": 640}, LoudnessMediaMismatchError),
            ({"output_rate": 24}, LoudnessMediaMismatchError),
            ({"output_duration": 11}, LoudnessDurationMismatchError),
        ]
        for options, error_type in cases:
            with self.subTest(error=error_type.__name__), TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "source.mp4"
                source.write_bytes(b"source")
                with self.assertRaises(error_type):
                    FFmpegLoudnessNormalizer(LoudnessRunner(), FakeProbe(source, **options)).normalize_loudness(source, root / "out.mp4", PROFILE)
                self.assertFalse((root / "out.mp4").exists())
                self.assertEqual(list(root.glob("*.part*")), [])

    def test_existing_destination_and_overwrite_behavior(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, destination = root / "source.mp4", root / "out.mp4"
            source.write_bytes(b"source")
            destination.write_bytes(b"old")
            runner = LoudnessRunner()
            with self.assertRaises(LoudnessDestinationExistsError):
                FFmpegLoudnessNormalizer(runner, FakeProbe(source)).normalize_loudness(source, destination, PROFILE)
            self.assertEqual(runner.calls, [])
            FFmpegLoudnessNormalizer(runner, FakeProbe(source)).normalize_loudness(source, destination, PROFILE, overwrite=True)
            self.assertEqual(destination.read_bytes(), CONTENT)


class LoudnessRunner:
    def __init__(self, measurements=None, analysis_text=None, analysis_exit=0, normalize_exit=0, content=CONTENT):
        self.measurements = MEASUREMENTS if measurements is None else measurements
        self.analysis_text = analysis_text
        self.analysis_exit = analysis_exit
        self.normalize_exit = normalize_exit
        self.content = content
        self.calls = []

    def run(self, args, timeout_seconds=None):
        args = list(args)
        self.calls.append(args)
        if len(self.calls) == 1:
            text = self.analysis_text if self.analysis_text is not None else "log\n" + json.dumps(self.measurements) + "\n"
            return ProcessResult(exit_code=self.analysis_exit, stdout="", stderr=text)
        Path(args[-1]).write_bytes(self.content)
        return ProcessResult(exit_code=self.normalize_exit, stdout="", stderr="safe error")


class FakeProbe:
    def __init__(self, source, source_audio=True, output_audio=True, output_width=1280, output_rate=30, output_duration=10):
        self.source = source
        self.source_audio = source_audio
        self.output_audio = output_audio
        self.output_width = output_width
        self.output_rate = output_rate
        self.output_duration = output_duration

    def probe_video(self, path):
        is_source = Path(path) == self.source
        return MediaProbeResult(local_path=path, duration_seconds=10 if is_source else self.output_duration, width=1280 if is_source else self.output_width, height=720, frame_rate=30 if is_source else self.output_rate, video_codec="h264", audio_codec="aac" if (self.source_audio if is_source else self.output_audio) else None, has_audio=self.source_audio if is_source else self.output_audio, container_format="mp4")
