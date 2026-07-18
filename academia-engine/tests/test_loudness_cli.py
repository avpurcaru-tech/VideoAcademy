from pathlib import Path
import unittest
from unittest.mock import patch

from app.cli.video_loudness_normalize import main
from app.media import LoudnessNormalizedVideoArtifact, MediaProbeResult


class LoudnessCliTests(unittest.TestCase):
    def test_cli_output_is_sanitized_and_omits_analysis(self) -> None:
        media = MediaProbeResult(local_path=Path("out.mp4"), duration_seconds=10, width=1280, height=720, frame_rate=30, video_codec="h264", audio_codec="aac", has_audio=True, container_format="mp4")
        artifact = LoudnessNormalizedVideoArtifact(local_path=Path("out.mp4"), byte_size=5, sha256="a" * 64, media_info=media)
        with patch("sys.argv", ["video_loudness_normalize", "--input", "in.mp4", "--output", "out.mp4"]), patch("app.cli.video_loudness_normalize.build_normalizer") as builder, patch("app.cli.video_loudness_normalize.print") as output:
            builder.return_value.normalize_loudness.return_value = artifact
            self.assertEqual(main(), 0)
        text = "\n".join(call.args[0] for call in output.call_args_list)
        self.assertIn("Has audio: true", text)
        self.assertIn("SHA-256: " + "a" * 64, text)
        for forbidden in ("input_i", "target_offset", "Authorization", "raw", "signed"):
            self.assertNotIn(forbidden, text)
