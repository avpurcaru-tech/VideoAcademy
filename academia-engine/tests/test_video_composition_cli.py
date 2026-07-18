from pathlib import Path
import unittest
from unittest.mock import patch

from app.cli.video_composition_show import main as show_main
from app.cli.video_composition_validate import main as validate_main
from app.composition import VideoCompositionManifest, VideoCompositionOutput, VideoCompositionScene


class VideoCompositionCliTests(unittest.TestCase):
    def test_show_prints_sanitized_resolved_scene_order(self) -> None:
        value = sample_manifest()
        with patch("sys.argv", ["video_composition_show", "--manifest", "composition.json"]), patch("app.cli.video_composition_show.load_manifest", return_value=value), patch("app.cli.video_composition_show.print") as output:
            self.assertEqual(show_main(), 0)
        text = "\n".join(call.args[0] for call in output.call_args_list)
        self.assertIn("Composition ID: episode-01", text)
        self.assertIn("Scenes: 2", text)
        self.assertLess(text.index("1. first -> one.mp4"), text.index("2. second -> two.mp4"))
        for forbidden in ("Kling", "signed", "Authorization", "provider payload", "task ID"):
            self.assertNotIn(forbidden, text)

    def test_validate_reports_only_contract_success_and_count(self) -> None:
        with patch("sys.argv", ["video_composition_validate", "--manifest", "composition.json"]), patch("app.cli.video_composition_validate.load_manifest", return_value=sample_manifest()), patch("app.cli.video_composition_validate.print") as output:
            self.assertEqual(validate_main(), 0)
        self.assertEqual([call.args[0] for call in output.call_args_list], ["Composition manifest is valid.", "Resolved scenes: 2"])


def sample_manifest():
    return VideoCompositionManifest(composition_id="episode-01", scenes=(VideoCompositionScene(scene_id="second", source_path=Path("two.mp4"), order=5), VideoCompositionScene(scene_id="first", source_path=Path("one.mp4"), order=1)), output=VideoCompositionOutput(destination=Path("final.mp4"), workspace=Path("work")))
