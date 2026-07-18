from pathlib import Path
import unittest
from unittest.mock import patch

from app.cli.video_timeline_render import main
from app.media import MediaProbeResult
from app.timeline import RenderedTimelineArtifact, TimelineRenderExecutionError
from tests.test_timeline_render_plan import validated, vscene
from tests.test_video_timeline_cli import sample_timeline


class TimelineRenderCliTests(unittest.TestCase):
    def test_success_output_is_sanitized_and_contains_only_durable_metadata(self) -> None:
        info = MediaProbeResult(local_path=Path("final.mp4"), duration_seconds=10, width=1280, height=720, frame_rate=30, video_codec="h264", audio_codec="aac", has_audio=True, container_format="mp4")
        artifact = RenderedTimelineArtifact(timeline_id="timeline-01", local_path=Path("final.mp4"), byte_size=5, sha256="a" * 64, media_info=info, source_count=2, transition_count=1)
        with patch("sys.argv", ["video_timeline_render", "--timeline", "timeline.json", "--timeout", "30"]), patch("app.cli.video_timeline_render.load_timeline", return_value=sample_timeline()), patch("app.cli.video_timeline_render.build_validator") as validator, patch("app.cli.video_timeline_render.build_renderer") as renderer, patch("app.cli.video_timeline_render.print") as output:
            validator.return_value.validate.return_value = validated([vscene("one", 0, 0, 5), vscene("two", 1, 0, 5)], total=10)
            renderer.return_value.render.return_value = artifact
            self.assertEqual(main(), 0)
        renderer.assert_called_once_with(30.0)
        text = "\n".join(call.args[0] for call in output.call_args_list)
        self.assertIn("Timeline ID: timeline-01", text)
        self.assertIn("Transitions: 1", text)
        self.assertIn("SHA-256: " + "a" * 64, text)
        for forbidden in ("part", "filter_complex", "ffmpeg", "-i", "raw", "Authorization", "signed"):
            self.assertNotIn(forbidden, text)

    def test_failure_output_is_sanitized(self) -> None:
        with patch("sys.argv", ["video_timeline_render", "--timeline", "timeline.json"]), patch("app.cli.video_timeline_render.load_timeline", return_value=sample_timeline()), patch("app.cli.video_timeline_render.build_validator") as validator, patch("app.cli.video_timeline_render.build_renderer") as renderer, patch("app.cli.video_timeline_render.print") as output:
            validator.return_value.validate.return_value = validated([vscene("one", 0, 0, 5), vscene("two", 1, 0, 5)], total=10)
            renderer.return_value.render.side_effect = TimelineRenderExecutionError("ffmpeg timeline render failed with exit code 1.")
            self.assertEqual(main(), 1)
        text = output.call_args.args[0]
        self.assertIn("exit code 1", text)
        for forbidden in ("filter_complex", "Authorization", "signed-secret", "raw stderr", ".part"):
            self.assertNotIn(forbidden, text)
