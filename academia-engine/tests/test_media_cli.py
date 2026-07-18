from pathlib import Path
import unittest
from unittest.mock import patch

from app.cli.video_normalize import main as normalize_main
from app.cli.video_probe import main as probe_main
from app.media import MediaProbeResult, NormalizedVideoArtifact, VideoNormalizationError


MEDIA = MediaProbeResult(local_path=Path("input.mp4"), duration_seconds=10, width=1280, height=720, frame_rate=30, video_codec="h264", audio_codec="aac", has_audio=True, container_format="mp4")


class MediaCliTests(unittest.TestCase):
    def test_probe_output_is_sanitized(self) -> None:
        with patch("sys.argv", ["video_probe", "--input", "input.mp4"]), patch("app.cli.video_probe.build_probe") as builder, patch("app.cli.video_probe.print") as output:
            builder.return_value.probe_video.return_value = MEDIA
            self.assertEqual(probe_main(), 0)
        text = "\n".join(call.args[0] for call in output.call_args_list)
        self.assertIn("Resolution: 1280x720", text)
        self.assertIn("Has audio: true", text)
        self.assertNotIn("raw", text)
        self.assertNotIn("Authorization", text)

    def test_normalization_output_is_sanitized(self) -> None:
        artifact = NormalizedVideoArtifact(local_path=Path("output.mp4"), byte_size=5, sha256="a" * 64, media_info=MEDIA.model_copy(update={"local_path": Path("output.mp4")}))
        with patch("sys.argv", ["video_normalize", "--input", "input.mp4", "--output", "output.mp4"]), patch("app.cli.video_normalize.build_normalizer") as builder, patch("app.cli.video_normalize.print") as output:
            builder.return_value.normalize_video.return_value = artifact
            self.assertEqual(normalize_main(), 0)
        text = "\n".join(call.args[0] for call in output.call_args_list)
        self.assertIn("Saved path: output.mp4", text)
        self.assertIn("SHA-256: " + "a" * 64, text)
        self.assertNotIn("signed", text)

    def test_error_output_is_bounded(self) -> None:
        bounded_error = VideoNormalizationError("ffmpeg failed; stderr: " + "x" * 500)
        with patch("sys.argv", ["video_normalize", "--input", "input.mp4", "--output", "output.mp4"]), patch("app.cli.video_normalize.build_normalizer") as builder, patch("app.cli.video_normalize.print") as output:
            builder.return_value.normalize_video.side_effect = bounded_error
            self.assertEqual(normalize_main(), 1)
        self.assertLessEqual(len(output.call_args.args[0]), 550)
