import unittest
from pathlib import Path
from types import SimpleNamespace

from app.media import (AudioCompositionSourceMissingError,CompositionDurationMismatchError,
    CompositionFFmpegError,CompositionOutputMissingError,CompositionPublicationError)
from app.project import ProjectGenerationService,ProjectStatus


class CompositionFailureDiagnosticTests(unittest.TestCase):
    def setUp(self): self.record=SimpleNamespace(status=ProjectStatus.COMPOSING)

    def test_known_composition_failures_have_exact_categories(self):
        ffmpeg=CompositionFFmpegError("safe"); ffmpeg.safe_category="unknown_ffmpeg_failure"; ffmpeg.exit_code=7
        unavailable=CompositionFFmpegError("safe"); unavailable.safe_category="ffmpeg_not_installed"
        cases=((AudioCompositionSourceMissingError("safe"),"composition_audio_variant_missing"),
            (CompositionDurationMismatchError("safe"),"composition_duration_mismatch"),
            (ffmpeg,"composition_ffmpeg_failed"),(unavailable,"composition_ffmpeg_unavailable"),
            (CompositionOutputMissingError("safe"),"composition_output_missing"),
            (CompositionPublicationError("safe"),"composition_output_persistence_failed"))
        for error,expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(expected,ProjectGenerationService._storyboard_run_failure_category(error,self.record))

    def test_ffmpeg_diagnostics_do_not_include_raw_stderr(self):
        error=CompositionFFmpegError("Audio/video composition failed."); error.exit_code=9; error.safe_category="invalid_filter_graph"
        self.assertEqual(9,error.exit_code); self.assertEqual("invalid_filter_graph",error.safe_category)
        self.assertNotIn("Authorization",str(error)); self.assertNotIn("stderr",str(error).lower())


if __name__=="__main__": unittest.main()
