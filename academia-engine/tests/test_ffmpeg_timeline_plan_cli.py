from pathlib import Path
import unittest
from unittest.mock import patch

from app.cli.video_timeline_ffmpeg_plan import main
from app.timeline import TimelineTransition, build_render_plan
from tests.test_timeline_render_plan import validated, vscene
from tests.test_video_timeline_cli import sample_timeline


class FFmpegTimelinePlanCliTests(unittest.TestCase):
    def test_cli_prints_structural_summary_without_command_or_graph_by_default(self) -> None:
        value = validated([vscene("one", 0, 0, 5, transition=TimelineTransition(kind="fade", duration_seconds=1)), vscene("two", 1, 0, 5)], total=9)
        with patch("sys.argv", ["video_timeline_ffmpeg_plan", "--timeline", "timeline.json"]), patch("app.cli.video_timeline_ffmpeg_plan.load_timeline", return_value=sample_timeline()), patch("app.cli.video_timeline_ffmpeg_plan.build_validator") as validator, patch("app.cli.video_timeline_ffmpeg_plan.print") as output:
            validator.return_value.validate.return_value = value
            self.assertEqual(main(), 0)
        text = "\n".join(call.args[0] for call in output.call_args_list)
        self.assertIn("Inputs: 2", text)
        self.assertIn("Audio output: yes", text)
        self.assertIn("Transitions: 1", text)
        self.assertNotIn("Filter graph:", text)
        for forbidden in ("-filter_complex", "ffmpeg -", "one.mp4", "Authorization", "signed"):
            self.assertNotIn(forbidden, text)

    def test_optional_filter_graph_contains_no_source_paths(self) -> None:
        value = validated([vscene("one", 0, 0, 5, audio=False, transition=TimelineTransition(kind="cut")), vscene("two", 1, 0, 5, audio=False)], total=10)
        with patch("sys.argv", ["video_timeline_ffmpeg_plan", "--timeline", "timeline.json", "--show-filter-graph"]), patch("app.cli.video_timeline_ffmpeg_plan.load_timeline", return_value=sample_timeline()), patch("app.cli.video_timeline_ffmpeg_plan.build_validator") as validator, patch("app.cli.video_timeline_ffmpeg_plan.print") as output:
            validator.return_value.validate.return_value = value
            self.assertEqual(main(), 0)
        text = "\n".join(call.args[0] for call in output.call_args_list)
        self.assertIn("Filter graph:", text)
        self.assertNotIn("one.mp4", text)
        self.assertNotIn("two.mp4", text)
