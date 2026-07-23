import io
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.cli import project_composition_preflight as cli
from app.project.composition_preflight import CompositionPreflightReport, CompositionVariantPreflight


def _variant(identifier: str) -> CompositionVariantPreflight:
    return CompositionVariantPreflight(
        variant_id=identifier,
        master_path=Path("video/master.mp4"), master_present=True, master_duration=60.0,
        audio_path=Path(f"music/{identifier}.mp3"), audio_present=True, audio_duration=90.0,
        timeline_path=Path(f"music/timeline-{identifier}.json"), timeline_present=True, timeline_duration=90.0,
        mapping_valid=True, duration_valid=True,
        expected_output_path=Path(f"final/final-{identifier}.mp4"), failure_category=None,
    )


class CompositionPreflightWiringTests(unittest.TestCase):
    def test_module_entrypoint_invokes_composition_preflight_service_for_both_variants(self):
        report = CompositionPreflightReport("project-1", (_variant("variant-01"), _variant("variant-02")))
        service = Mock()
        service.inspect.return_value = report
        output = io.StringIO()
        with patch.object(cli, "CompositionPreflightService", return_value=service), \
                patch.object(cli, "build_probe", return_value=Mock()), \
                patch.object(sys, "argv", ["project_composition_preflight", "--project-id", "project-1"]), \
                patch("sys.stdout", output):
            self.assertEqual(cli.main(), 0)
        service.inspect.assert_called_once_with("project-1")
        rendered = output.getvalue()
        self.assertIn("Variant: variant-01", rendered)
        self.assertIn("Variant: variant-02", rendered)
        self.assertNotIn("Video production status", rendered)
        self.assertNotIn("Ready scenes", rendered)
        self.assertIn("Provider calls: 0", rendered)
        self.assertIn("FFmpeg calls: 0", rendered)

    def test_invalid_variant_prints_exact_category_and_failed_variant(self):
        invalid = _variant("variant-01")
        invalid = CompositionVariantPreflight(**{**invalid.__dict__, "failure_category": "composition_timeline_missing"})
        report = CompositionPreflightReport("project-1", (invalid, _variant("variant-02")))
        service = Mock(); service.inspect.return_value = report
        output = io.StringIO()
        with patch.object(cli, "CompositionPreflightService", return_value=service), \
                patch.object(cli, "build_probe", return_value=Mock()), \
                patch.object(sys, "argv", ["project_composition_preflight", "--project-id", "project-1"]), \
                patch("sys.stdout", output):
            self.assertEqual(cli.main(), 1)
        self.assertIn("Failure category: composition_timeline_missing", output.getvalue())
        self.assertIn("Failed variant: variant-01", output.getvalue())
        self.assertNotIn("composition_failed", output.getvalue())


if __name__ == "__main__":
    unittest.main()
