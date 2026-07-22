import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch

from pydantic import ValidationError

from app.cli.project_storyboard_preflight import main as preflight_main
from app.creative import EducationalCreativeBrief
from app.project import CreativeProjectGenerationService,ProjectGenerationService,ProjectRegistry
from app.providers.openai_storyboard_provider import (OpenAIStoryboardGenerator,OpenAIStoryboardRefusalError,
    OpenAIStoryboardStructuredOutputMalformedError,OpenAIStoryboardStructuredOutputMissingError)
from app.storyboard import DeterministicStoryboardGenerator,StoryboardGenerationService,StoryboardSeriesContinuityError
from tests.test_series_bible import _ProfileRegistry,_Registry,bible_payload,brief_payload,profiles
from app.series import SeriesBible


class _Generator:
    def __init__(self,value): self.value=value
    def generate_storyboard(self,*args,**kwargs): return self.value


class StoryboardRecoveryDiagnosticTests(unittest.TestCase):
    def test_missing_parsed_output_and_refusal_are_distinct_and_suppress_raw_text(self):
        client=Mock(); client.responses.parse.return_value=SimpleNamespace(output_parsed=None,output=())
        with self.assertRaises(OpenAIStoryboardStructuredOutputMissingError):
            OpenAIStoryboardGenerator(client=client).generate_storyboard(EducationalCreativeBrief.model_validate(brief_payload()))
        refusal=SimpleNamespace(type="refusal",refusal="RAW SECRET REFUSAL")
        client.responses.parse.return_value=SimpleNamespace(output_parsed=None,output=(SimpleNamespace(content=(refusal,)),))
        with self.assertRaises(OpenAIStoryboardRefusalError) as raised:
            OpenAIStoryboardGenerator(client=client).generate_storyboard(EducationalCreativeBrief.model_validate(brief_payload()))
        self.assertNotIn("RAW SECRET",str(raised.exception))

    def test_malformed_dto_is_distinct(self):
        client=Mock()
        def malformed(**kwargs):
            from app.storyboard import CreativeStoryboard
            return CreativeStoryboard.model_validate({})
        client.responses.parse.side_effect=malformed
        with self.assertRaises(OpenAIStoryboardStructuredOutputMalformedError):
            OpenAIStoryboardGenerator(client=client).generate_storyboard(EducationalCreativeBrief.model_validate(brief_payload()))

    def test_semantic_character_categories_and_id_only_acceptance(self):
        bible=SeriesBible.model_validate(bible_payload()); brief=EducationalCreativeBrief.model_validate(brief_payload())
        base=DeterministicStoryboardGenerator().generate_storyboard(brief,bible,profiles())
        StoryboardGenerationService(_Generator(base),_Registry(bible),_ProfileRegistry()).generate(brief)
        cases=(("storyboard_missing_required_character",("luca",)),
               ("storyboard_unknown_character",("luca","max","intruder")))
        for category,ids in cases:
            section=base.sections[0].model_copy(update={"characters":ids})
            changed=base.model_copy(update={"sections":(section,)+base.sections[1:]})
            with self.assertRaises(StoryboardSeriesContinuityError) as raised:
                StoryboardGenerationService(_Generator(changed),_Registry(bible),_ProfileRegistry()).generate(brief)
            self.assertEqual(category,raised.exception.failure_category)

    def test_max_dialogue_has_safe_field_path(self):
        bible=SeriesBible.model_validate(bible_payload()); brief=EducationalCreativeBrief.model_validate(brief_payload())
        base=DeterministicStoryboardGenerator().generate_storyboard(brief,bible,profiles())
        section=base.sections[0].model_copy(update={"lyrics":"Max: secret raw dialogue"})
        with self.assertRaises(StoryboardSeriesContinuityError) as raised:
            StoryboardGenerationService(_Generator(base.model_copy(update={"sections":(section,)+base.sections[1:]})),
                _Registry(bible),_ProfileRegistry()).generate(brief)
        self.assertEqual("storyboard_max_speaks",raised.exception.failure_category)
        self.assertEqual(("sections.1.dialogue: Max must not speak",),raised.exception.failure_details)
        self.assertNotIn("secret raw dialogue",str(raised.exception.failure_details))

    def test_brief_and_safe_character_references_are_persisted_atomically(self):
        brief=EducationalCreativeBrief.model_validate(brief_payload())
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/"recoverable"; registry=ProjectRegistry(root.parent)
            record=ProjectGenerationService.create_planned(registry,"recoverable",root,brief.brief_id,brief.series_id)
            ProjectGenerationService.persist_creative_brief(record,brief,("luca","max"))
            payload=json.loads((root/"input"/"creative-brief.json").read_text(encoding="utf-8"))
            self.assertEqual(["luca","max"],payload["resolved_character_ids"])
            self.assertNotIn("prompt",json.dumps(payload).lower())

    def test_preflight_reports_missing_legacy_input_without_constructing_openai(self):
        record=Mock(project_id="legacy",lyrics_path=Path("missing")/"lyrics"/"lyrics.json")
        with patch("sys.argv",["project_storyboard_preflight","--project-id","legacy"]),patch(
                "app.cli.project_storyboard_preflight.ProjectRegistry") as projects,patch(
                "app.cli.project_storyboard_preflight.OpenAIStoryboardGenerator") as provider,patch("builtins.print") as emit:
            projects.return_value.load.return_value=record; self.assertEqual(1,preflight_main())
        provider.assert_not_called()
        self.assertIn("non-resumable-input-missing","\n".join(str(c.args[0]) for c in emit.call_args_list))


if __name__=="__main__": unittest.main()
