from pathlib import Path
import unittest
from unittest.mock import patch

from app.cli.video_composition_execute import main
from app.composition import CompositionAssemblyError, CompositionExecutionResult
from app.media import MediaProbeResult
from tests.test_video_composition_cli import sample_manifest


class CompositionExecuteCliTests(unittest.TestCase):
    def test_success_output_is_sanitized_and_contains_no_intermediates(self) -> None:
        media = MediaProbeResult(local_path=Path("final.mp4"), duration_seconds=20, width=1280, height=720, frame_rate=30, video_codec="h264", audio_codec="aac", has_audio=True, container_format="mp4")
        result = CompositionExecutionResult(composition_id="episode-01", local_path=Path("final.mp4"), byte_size=5, sha256="a" * 64, media_info=media, source_count=2)
        with patch("sys.argv", ["video_composition_execute", "--manifest", "composition.json"]), patch("app.cli.video_composition_execute.load_manifest", return_value=sample_manifest()), patch("app.cli.video_composition_execute.build_execution_service") as builder, patch("app.cli.video_composition_execute.print") as output:
            builder.return_value.execute.return_value = result
            self.assertEqual(main(), 0)
        builder.return_value.execute.assert_called_once_with(sample_manifest(), overwrite=False)
        text = "\n".join(call.args[0] for call in output.call_args_list)
        self.assertIn("Composition ID: episode-01", text)
        self.assertIn("Saved path: final.mp4", text)
        for forbidden in ("scene_0001", "concatenated.mp4", "workspace", "Authorization", "signed", "ffmpeg"):
            self.assertNotIn(forbidden, text)

    def test_failure_output_does_not_leak_lower_level_details(self) -> None:
        error = CompositionAssemblyError("Composition assembly failed: episode-01")
        with patch("sys.argv", ["video_composition_execute", "--manifest", "composition.json", "--overwrite"]), patch("app.cli.video_composition_execute.load_manifest", return_value=sample_manifest()), patch("app.cli.video_composition_execute.build_execution_service") as builder, patch("app.cli.video_composition_execute.print") as output:
            builder.return_value.execute.side_effect = error
            self.assertEqual(main(), 1)
        text = output.call_args.args[0]
        self.assertIn("episode-01", text)
        for forbidden in ("Authorization", "signed-secret", "raw stderr", "intermediate"):
            self.assertNotIn(forbidden, text)
