from pathlib import Path
import unittest
from unittest.mock import patch

from app.cli.video_concat import main
from app.media import ConcatenatedVideoArtifact, MediaProbeResult


class VideoConcatCliTests(unittest.TestCase):
    def test_cli_preserves_repeated_input_order_and_prints_sanitized_output(self) -> None:
        media = MediaProbeResult(local_path=Path("final.mp4"), duration_seconds=4, width=1280, height=720, frame_rate=30, video_codec="h264", audio_codec="aac", has_audio=True, container_format="mp4")
        artifact = ConcatenatedVideoArtifact(local_path=Path("final.mp4"), byte_size=5, sha256="a" * 64, media_info=media, source_count=2)
        arguments = ["video_concat", "--input", "scene one.mp4", "--input", "scenă două.mp4", "--output", "final.mp4"]
        with patch("sys.argv", arguments), patch("app.cli.video_concat.build_concatenator") as builder, patch("app.cli.video_concat.print") as output:
            builder.return_value.concatenate_videos.return_value = artifact
            self.assertEqual(main(), 0)
        builder.return_value.concatenate_videos.assert_called_once_with([Path("scene one.mp4"), Path("scenă două.mp4")], Path("final.mp4"))
        text = "\n".join(call.args[0] for call in output.call_args_list)
        self.assertIn("Sources: 2", text)
        self.assertIn("Resolution: 1280x720", text)
        for forbidden in ("manifest", "Authorization", "signed", "payload", "billing"):
            self.assertNotIn(forbidden, text)
