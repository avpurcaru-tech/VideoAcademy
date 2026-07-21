from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app.cli.storyboard_generate import main as cli_main
from app.creative import EducationalCreativeBrief
from app.providers.openai_storyboard_provider import OpenAIStoryboardGenerator
from app.storyboard import (CreativeStoryboard, DeterministicStoryboardGenerator, StoryboardAudience,
    StoryboardGenerationService, StoryboardGenerator, StoryboardLanguageMismatchError,
    StoryboardAudienceMismatchError, StoryboardRepository, StoryboardSection)


def brief():
    return EducationalCreativeBrief(brief_id="counting-story", topic="counting to three",
        learning_objectives=("recognize numbers", "count objects"), language="ro",
        target_age_min=3, target_age_max=5, target_duration_seconds=30, tone="cheerful",
        visual_style="simple colorful animation", main_character_hint="Bibi", location_hint="garden",
        scene_count=3, song_required=True)


def storyboard_payload():
    return DeterministicStoryboardGenerator().generate_storyboard(brief()).model_dump(mode="python")


class CreativeStoryboardTests(unittest.TestCase):
    def test_protocol_and_deterministic_generation(self):
        generator = DeterministicStoryboardGenerator()
        self.assertIsInstance(generator, StoryboardGenerator)
        first = StoryboardGenerationService(generator).generate(brief())
        second = StoryboardGenerationService(generator).generate(brief())
        self.assertEqual(first, second)
        self.assertEqual([section.order for section in first.sections], [1, 2, 3])
        self.assertEqual(sum(section.estimated_duration_seconds for section in first.sections), 30)

    def test_complete_contract_validation(self):
        mutations = []
        duplicate = storyboard_payload(); duplicate["sections"][1]["section_id"] = duplicate["sections"][0]["section_id"]
        mutations.append(duplicate)
        order = storyboard_payload(); order["sections"][1]["order"] = 3
        mutations.append(order)
        duration = storyboard_payload(); duration["sections"][0]["estimated_duration_seconds"] = 0
        mutations.append(duration)
        total = storyboard_payload(); total["target_duration_seconds"] = 99
        mutations.append(total)
        for payload in mutations:
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                CreativeStoryboard.model_validate(payload)

    def test_service_rejects_language_and_audience_mismatch(self):
        for update, expected in (({"language": "en"}, StoryboardLanguageMismatchError),
            ({"audience": StoryboardAudience(target_age_min=6, target_age_max=8)}, StoryboardAudienceMismatchError)):
            generated = CreativeStoryboard.model_validate(storyboard_payload()).model_copy(update=update)
            fake = type("Fake", (), {"generate_storyboard": lambda self, value, result=generated: result})()
            with self.assertRaises(expected): StoryboardGenerationService(fake).generate(brief())

    def test_atomic_persistence_uses_authoritative_path_and_round_trips(self):
        value = CreativeStoryboard.model_validate(storyboard_payload())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".runtime" / "storyboards"
            with patch("app.storyboard.repository.os.replace", wraps=__import__("os").replace) as replace:
                destination = StoryboardRepository(root).save(value)
            self.assertEqual(destination, root / "counting-story" / "storyboard.json")
            self.assertEqual(StoryboardRepository(root).load("counting-story"), value)
            self.assertFalse(destination.with_suffix(".json.part").exists())
            replace.assert_called_once()

    def test_contract_is_provider_neutral(self):
        schema = json.dumps(CreativeStoryboard.model_json_schema()).lower()
        fields = set(CreativeStoryboard.model_fields)
        self.assertEqual(fields, {"storyboard_id", "title", "language", "audience", "educational_goal",
                                  "target_duration_seconds", "sections"})
        for provider in ("kling", "suno", "openai", "prompt", "payload", "model"):
            self.assertNotIn(provider, schema)

    def test_openai_adapter_uses_structured_output_without_real_call(self):
        expected = CreativeStoryboard.model_validate(storyboard_payload())
        response = type("Response", (), {"output_parsed": expected})()
        client = type("Client", (), {})(); client.responses = unittest.mock.Mock()
        client.responses.parse.return_value = response
        result = OpenAIStoryboardGenerator(client=client).generate_storyboard(brief())
        self.assertEqual(result, expected)
        kwargs = client.responses.parse.call_args.kwargs
        self.assertEqual(kwargs["text_format"], CreativeStoryboard)

    def test_openai_cli_without_confirm_never_resolves_or_calls_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); brief_path = root / "brief.json"
            brief_path.write_text(brief().model_dump_json(), encoding="utf-8")
            argv = ["storyboard_generate", "--brief", str(brief_path), "--generator", "openai",
                    "--runtime-root", str(root / "storyboards")]
            with patch("sys.argv", argv), patch("app.cli.storyboard_generate.StoryboardGeneratorRegistry") as registry:
                self.assertEqual(cli_main(), 2)
            registry.assert_not_called()
            self.assertFalse((root / "storyboards").exists())

    def test_deterministic_cli_persists_default_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); brief_path = root / "brief.json"; runtime = root / "storyboards"
            brief_path.write_text(brief().model_dump_json(), encoding="utf-8")
            argv = ["storyboard_generate", "--brief", str(brief_path), "--generator", "deterministic",
                    "--runtime-root", str(runtime)]
            with patch("sys.argv", argv): self.assertEqual(cli_main(), 0)
            self.assertTrue((runtime / "counting-story" / "storyboard.json").is_file())


if __name__ == "__main__": unittest.main()
