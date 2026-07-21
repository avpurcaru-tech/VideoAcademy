import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

from app.characters import CharacterRegistryError
from app.cli.project_generate_from_brief import main as creative_cli
from app.cli.project_resume import _failure
from app.creative import EducationalCreativeBrief
from app.project import (CreativeProjectGenerationService,ProjectFailureStage,
                         ProjectGenerationService,ProjectRegistry,ProjectStatus)
from app.providers.openai_episode_provider import OpenAIEpisodeConfigurationError
from app.storyboard import InvalidStoryboardError


class EarlyProjectPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(); self.base=Path(self.temporary.name)
        source=Path(__file__).resolve().parents[1]/"examples"/"smoke"/"creative-brief.json"
        self.brief=EducationalCreativeBrief.model_validate_json(source.read_text(encoding="utf-8"))

    def tearDown(self): self.temporary.cleanup()

    def _brief_file(self,brief):
        path=self.base/"brief.json"; path.write_text(brief.model_dump_json(indent=2),encoding="utf-8"); return path

    def _argv(self,brief,project_id="early-project",generator="deterministic"):
        return ["project_generate_from_brief","--brief",str(self._brief_file(brief)),"--project-id",project_id,
                "--episode-generator",generator,"--output",str(self.base/project_id),"--confirm"]

    def test_character_failure_persists_before_any_provider_is_constructed_and_resume_reports_it(self):
        brief=self.brief.model_copy(update={"series_id":"series-one"}); registry=ProjectRegistry(self.base)
        with patch("sys.argv",self._argv(brief)),patch("app.cli.project_generate_from_brief.load_application_environment"),patch(
                "app.cli.project_generate_from_brief.CharacterRegistry",side_effect=CharacterRegistryError("private")),patch(
                "app.cli.project_generate_from_brief.EpisodeGeneratorRegistry") as episodes,patch(
                "app.cli.project_generate_from_brief.build_services") as providers,patch("builtins.print"):
            self.assertEqual(creative_cli(),1)
        record=registry.load("early-project")
        self.assertEqual(record.status,ProjectStatus.FAILED)
        self.assertEqual(record.failure_stage,ProjectFailureStage.CHARACTER_RESOLUTION)
        self.assertTrue((self.base/"early-project"/"project.json").is_file())
        episodes.assert_not_called(); providers.assert_not_called()
        with patch("builtins.print") as emit: _failure(record)
        output="\n".join(str(call.args[0]) for call in emit.call_args_list)
        self.assertIn("character_resolution",output); self.assertIn(record.safe_message,output)

    def test_storyboard_validation_failure_keeps_resumable_record(self):
        registry=ProjectRegistry(self.base); root=self.base/"storyboard-project"
        ProjectGenerationService.create_planned(registry,"storyboard-project",root,self.brief.brief_id)
        storyboards=Mock(); storyboards.generate.side_effect=InvalidStoryboardError("private")
        service=CreativeProjectGenerationService(Mock(),Mock(),registry,storyboards)
        series_brief=self.brief.model_copy(update={"series_id":"series-one"})
        with self.assertRaises(InvalidStoryboardError): service.generate(series_brief,"storyboard-project",root,Mock(),Mock())
        record=registry.load("storyboard-project")
        self.assertEqual(record.status,ProjectStatus.FAILED)
        self.assertEqual(record.failure_stage,ProjectFailureStage.STORYBOARD_GENERATION)
        with patch("builtins.print") as emit: _failure(record)
        self.assertIn("storyboard_generation","\n".join(str(c.args[0]) for c in emit.call_args_list))

    def test_openai_configuration_failure_is_durable_and_reported(self):
        registry=ProjectRegistry(self.base)
        episode_registry=Mock(); episode_registry.resolve.side_effect=OpenAIEpisodeConfigurationError("private")
        with patch("sys.argv",self._argv(self.brief,generator="openai")),patch(
                "app.cli.project_generate_from_brief.load_application_environment"),patch(
                "app.cli.project_generate_from_brief.EpisodeGeneratorRegistry",return_value=episode_registry),patch(
                "app.cli.project_generate_from_brief.build_services") as providers,patch("builtins.print"):
            self.assertEqual(creative_cli(),1)
        record=registry.load("early-project")
        self.assertEqual(record.status,ProjectStatus.FAILED)
        self.assertEqual(record.failure_category,"provider_configuration_failed")
        providers.assert_not_called()
        with patch("builtins.print") as emit: _failure(record)
        output="\n".join(str(call.args[0]) for call in emit.call_args_list)
        self.assertIn("provider_configuration_failed",output)
        self.assertNotIn("private",json.dumps(record.model_dump(mode="json")))


if __name__=="__main__": unittest.main()
