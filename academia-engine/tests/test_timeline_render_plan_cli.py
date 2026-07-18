from pathlib import Path
import unittest
from unittest.mock import patch

from app.cli.video_timeline_render_plan import main
from app.media import MediaProbeResult
from app.timeline import TimelineTransition, ValidatedTimelineScene, ValidatedVideoTimeline
from tests.test_video_timeline_cli import sample_timeline


class TimelineRenderPlanCliTests(unittest.TestCase):
    def test_cli_prints_sanitized_semantic_plan(self) -> None:
        info = MediaProbeResult(local_path=Path("one.mp4"), duration_seconds=10, width=1280, height=720, frame_rate=30, video_codec="h264", audio_codec="aac", has_audio=True, container_format="mp4")
        validated = ValidatedVideoTimeline(timeline_id="timeline-01", scenes=(ValidatedTimelineScene(scene_id="first", source_path=Path("one.mp4"), order=0, source_media_info=info, effective_start_seconds=1, effective_end_seconds=6, effective_duration_seconds=5, transition_to_next=TimelineTransition(kind="fade", duration_seconds=1)), ValidatedTimelineScene(scene_id="second", source_path=Path("two.mp4"), order=1, source_media_info=info.model_copy(update={"local_path": Path("two.mp4"), "has_audio": False, "audio_codec": None}), effective_start_seconds=0, effective_end_seconds=5, effective_duration_seconds=5, transition_to_next=None)), destination=Path("final.mp4"), workspace=Path("work"), source_count=2, total_duration_seconds=9)
        with patch("sys.argv", ["video_timeline_render_plan", "--timeline", "timeline.json"]), patch("app.cli.video_timeline_render_plan.load_timeline", return_value=sample_timeline()), patch("app.cli.video_timeline_render_plan.build_validator") as validator, patch("app.cli.video_timeline_render_plan.print") as output:
            validator.return_value.validate.return_value = validated
            self.assertEqual(main(), 0)
        text = "\n".join(call.args[0] for call in output.call_args_list)
        self.assertIn("Transitions: 1", text)
        self.assertIn("Expected duration: 9.0", text)
        self.assertIn("Source range: 1.0 -> 6.0", text)
        self.assertIn("Output range: 0.0 -> 5.0", text)
        self.assertIn("first -> second", text)
        self.assertIn("Kind: fade", text)
        for forbidden in ("ffmpeg", "filter_complex", "setpts", "xfade", "raw", "Authorization", "signed"):
            self.assertNotIn(forbidden, text)
