from pathlib import Path
import unittest
from unittest.mock import patch

from app.cli.video_timeline_media_validate import main
from app.media import MediaProbeResult
from app.timeline import TimelineTransition, ValidatedTimelineScene, ValidatedVideoTimeline
from tests.test_video_timeline_cli import sample_timeline


class TimelineMediaValidateCliTests(unittest.TestCase):
    def test_cli_prints_sanitized_validated_ranges_without_raw_probe_output(self) -> None:
        info = MediaProbeResult(local_path=Path("one.mp4"), duration_seconds=10, width=1280, height=720, frame_rate=30, video_codec="h264", audio_codec="aac", has_audio=True, container_format="mp4")
        scenes = (
            ValidatedTimelineScene(scene_id="first", source_path=Path("one.mp4"), order=1, source_media_info=info, effective_start_seconds=1, effective_end_seconds=4, effective_duration_seconds=3, transition_to_next=TimelineTransition(kind="fade", duration_seconds=0.5)),
            ValidatedTimelineScene(scene_id="second", source_path=Path("two.mp4"), order=2, source_media_info=info.model_copy(update={"local_path": Path("two.mp4")}), effective_start_seconds=0, effective_end_seconds=10, effective_duration_seconds=10, transition_to_next=None),
        )
        result = ValidatedVideoTimeline(timeline_id="timeline-01", scenes=scenes, destination=Path("final.mp4"), workspace=Path("work"), source_count=2, total_duration_seconds=12.5)
        with patch("sys.argv", ["video_timeline_media_validate", "--timeline", "timeline.json"]), patch("app.cli.video_timeline_media_validate.load_timeline", return_value=sample_timeline()), patch("app.cli.video_timeline_media_validate.build_validator") as builder, patch("app.cli.video_timeline_media_validate.print") as output:
            builder.return_value.validate.return_value = result
            self.assertEqual(main(), 0)
        text = "\n".join(call.args[0] for call in output.call_args_list)
        self.assertIn("Total duration: 12.5", text)
        self.assertIn("Effective range: 1.0 -> 4.0", text)
        self.assertIn("Transition: fade (0.5)", text)
        for forbidden in ("raw", "streams", "format_name", "ffprobe", "Authorization", "signed"):
            self.assertNotIn(forbidden, text)
