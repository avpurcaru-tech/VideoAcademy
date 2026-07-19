import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.cli.episode_plan import main
from app.production import EpisodeProductionPromptBuilderError, GenerationRequestNotFoundError
from tests.test_episode_production_planner import plan


class EpisodePlanCliTests(unittest.TestCase):
    def test_missing_malformed_and_director_schema_diagnostics_are_sanitized(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); missing=root/"missing.json"; malformed=root/"bad.json"; malformed.write_text('{"prompt":"secret"')
            invalid=root/"invalid.json"; invalid.write_text('{"episode_id":"safe","episode_title":"title","scenes":[{"scene_number":"secret","description":"credential"}]}')
            for path,expected in ((missing,"Input file not found"),(malformed,"Input JSON is malformed"),(invalid,"DirectorPlan validation failed")):
                with self.subTest(expected=expected),patch("sys.argv",self.argv(path)),patch("app.cli.episode_plan.build_planner") as builder,patch("builtins.print") as emit:
                    self.assertEqual(main(),1); builder.assert_not_called()
                    output=" ".join(str(call.args[0]) for call in emit.call_args_list); self.assertIn(expected,output)
                    for forbidden in ("prompt","secret","credential",'"description"'): self.assertNotIn(forbidden,output)

    def test_successful_preflight_is_non_mutating_and_prints_safe_input_type(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/"director.json"; source.write_text(plan((1,2)).model_dump_json())
            from app.production import EpisodeProductionPlanner, GenerationRequestStore
            from app.prompts import PromptBuilder
            from tests.test_episode_production_planner import RecordingAdapter
            store=GenerationRequestStore(root/"requests"); planner=EpisodeProductionPlanner(PromptBuilder(RecordingAdapter()),store)
            with patch("sys.argv",self.argv(source)+["--preflight"]),patch("app.cli.episode_plan.build_planner",return_value=planner),patch("builtins.print") as emit:
                self.assertEqual(main(),0)
            self.assertFalse((root/"requests").exists())
            output=" ".join(str(call.args[0]) for call in emit.call_args_list)
            self.assertIn("Planning preflight passed",output); self.assertIn("Semantic input: DirectorPlan",output)
            self.assertIn("episode-001-scene-0001",output); self.assertNotIn("Semantic scene description",output)

    def test_prompt_builder_failure_is_safe_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/"director.json"; source.write_text(plan((1,2)).model_dump_json())
            planner=Mock(); planner.preflight.side_effect=EpisodeProductionPromptBuilderError("raw prompt signed URL Authorization")
            with patch("sys.argv",self.argv(source)+["--preflight"]),patch("app.cli.episode_plan.build_planner",return_value=planner),patch("builtins.print") as emit:
                self.assertEqual(main(),1)
            planner.persist.assert_not_called()
            output=" ".join(str(call.args[0]) for call in emit.call_args_list); self.assertIn("PromptBuilder failed",output)
            for forbidden in ("raw prompt","signed URL","Authorization"): self.assertNotIn(forbidden,output)

    @staticmethod
    def argv(path):
        return ["episode_plan","--input",str(path),"--production-id","episode-001","--scene-output-dir","scenes",
                "--workspace","media","--output","final.mp4","--transition","fade","--transition-duration","0.5"]


if __name__=="__main__": unittest.main()
