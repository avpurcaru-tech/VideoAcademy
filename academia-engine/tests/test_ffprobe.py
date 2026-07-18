import json
from pathlib import Path
import unittest

from app.media import FFprobeAdapter, FFprobeExecutionError, FFprobeResponseError, ProcessResult


class FFprobeAdapterTests(unittest.TestCase):
    def test_valid_video_with_audio_is_normalized(self) -> None:
        runner = FakeRunner(result(payload()))
        media = FFprobeAdapter(runner).probe_video(Path("input video.mp4"))
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(runner.calls[0][0], ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", "input video.mp4"])
        self.assertEqual(media.duration_seconds, 12.5)
        self.assertEqual((media.width, media.height), (1280, 720))
        self.assertAlmostEqual(media.frame_rate, 29.97002997)
        self.assertEqual(media.video_codec, "h264")
        self.assertEqual(media.audio_codec, "aac")
        self.assertTrue(media.has_audio)
        self.assertEqual(media.container_format, "mov,mp4")

    def test_valid_video_without_audio(self) -> None:
        data = payload()
        data["streams"] = data["streams"][:1]
        media = FFprobeAdapter(FakeRunner(result(data))).probe_video(Path("silent.mp4"))
        self.assertFalse(media.has_audio)
        self.assertIsNone(media.audio_codec)

    def test_missing_video_stream_is_rejected(self) -> None:
        data = payload()
        data["streams"] = data["streams"][1:]
        with self.assertRaises(FFprobeResponseError):
            FFprobeAdapter(FakeRunner(result(data))).probe_video(Path("audio.m4a"))

    def test_malformed_json_is_rejected(self) -> None:
        with self.assertRaises(FFprobeResponseError):
            FFprobeAdapter(FakeRunner(ProcessResult(exit_code=0, stdout="{bad", stderr=""))).probe_video(Path("x.mp4"))

    def test_missing_duration_and_invalid_frame_rate_are_rejected(self) -> None:
        missing = payload()
        del missing["format"]["duration"]
        invalid = payload()
        invalid["streams"][0]["avg_frame_rate"] = "0/0"
        for data in (missing, invalid):
            with self.subTest(data=data), self.assertRaises(FFprobeResponseError):
                FFprobeAdapter(FakeRunner(result(data))).probe_video(Path("x.mp4"))

    def test_nonzero_exit_code_has_bounded_sanitized_diagnostics(self) -> None:
        error_text = "failure " * 200
        with self.assertRaises(FFprobeExecutionError) as caught:
            FFprobeAdapter(FakeRunner(ProcessResult(exit_code=7, stdout="secret raw json", stderr=error_text))).probe_video(Path("x.mp4"))
        self.assertIn("exit code 7", str(caught.exception))
        self.assertLessEqual(len(str(caught.exception)), 550)
        self.assertNotIn("raw json", str(caught.exception))


class FakeRunner:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def run(self, args, timeout_seconds=None):
        self.calls.append((list(args), timeout_seconds))
        return self.response


def payload():
    return {
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720, "avg_frame_rate": "30000/1001"},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
        "format": {"duration": "12.5", "format_name": "mov,mp4"},
    }


def result(data):
    return ProcessResult(exit_code=0, stdout=json.dumps(data), stderr="")
