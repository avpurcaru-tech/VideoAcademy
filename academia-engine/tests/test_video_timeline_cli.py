import unittest
from unittest.mock import patch

from app.cli.video_timeline_show import main as show_main
from app.cli.video_timeline_validate import main as validate_main
from app.timeline import TimelineOutput, TimelineScene, TimelineTransition, VideoTimeline


class VideoTimelineCliTests(unittest.TestCase):
    def test_show_prints_sanitized_resolved_semantics(self) -> None:
        value = sample_timeline()
        with patch("sys.argv", ["video_timeline_show", "--timeline", "timeline.json"]), patch("app.cli.video_timeline_show.load_timeline", return_value=value), patch("app.cli.video_timeline_show.print") as output:
            self.assertEqual(show_main(), 0)
        text = "\n".join(call.args[0] for call in output.call_args_list)
        self.assertIn("Timeline ID: timeline-01", text)
        self.assertLess(text.index("1. first"), text.index("2. second"))
        self.assertIn("Trim: 1.0 -> 4.0", text)
        self.assertIn("Transition: fade (0.5)", text)
        for forbidden in ("ffmpeg", "filter_complex", "xfade", "Kling", "Authorization", "signed"):
            self.assertNotIn(forbidden, text)

    def test_validate_prints_only_success_and_resolved_count(self) -> None:
        with patch("sys.argv", ["video_timeline_validate", "--timeline", "timeline.json"]), patch("app.cli.video_timeline_validate.load_timeline", return_value=sample_timeline()), patch("app.cli.video_timeline_validate.print") as output:
            self.assertEqual(validate_main(), 0)
        self.assertEqual([call.args[0] for call in output.call_args_list], ["Timeline is valid.", "Resolved scenes: 2"])


def sample_timeline():
    return VideoTimeline(timeline_id="timeline-01", scenes=(TimelineScene(scene_id="second", source_path="two.mp4", order=5), TimelineScene(scene_id="first", source_path="one.mp4", order=1, trim_start_seconds=1, trim_end_seconds=4, transition_to_next=TimelineTransition(kind="fade", duration_seconds=0.5))), output=TimelineOutput(destination="final.mp4", workspace="work"))
