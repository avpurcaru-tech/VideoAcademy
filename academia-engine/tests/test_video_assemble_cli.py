from pathlib import Path
import unittest
from unittest.mock import patch

from app.cli.video_assemble import main
from app.media import AssembledVideoArtifact, MediaProbeResult


class VideoAssembleCliTests(unittest.TestCase):
    def test_cli_preserves_input_order_and_prints_only_final_metadata(self) -> None:
        media = MediaProbeResult(local_path=Path("final.mp4"), duration_seconds=20, width=1280, height=720, frame_rate=30, video_codec="h264", audio_codec="aac", has_audio=True, container_format="mp4")
        artifact = AssembledVideoArtifact(local_path=Path("final.mp4"), byte_size=5, sha256="a" * 64, media_info=media, source_count=2)
        arguments = ["video_assemble", "--input", "scene two.mp4", "--input", "scene one.mp4", "--workspace", ".runtime/media/work", "--output", "final.mp4"]
        with patch("sys.argv", arguments), patch("app.cli.video_assemble.build_assembly_service") as builder, patch("app.cli.video_assemble.print") as output:
            builder.return_value.assemble.return_value = artifact
            self.assertEqual(main(), 0)
        request = builder.return_value.assemble.call_args.args[0]
        self.assertEqual(request.sources, (Path("scene two.mp4"), Path("scene one.mp4")))
        text = "\n".join(call.args[0] for call in output.call_args_list)
        self.assertIn("Saved path: final.mp4", text)
        self.assertIn("Sources: 2", text)
        self.assertIn("Has audio: true", text)
        for forbidden in ("scene_0001", "concatenated.mp4", ".runtime/media/work", "Authorization", "signed", "payload"):
            self.assertNotIn(forbidden, text)
