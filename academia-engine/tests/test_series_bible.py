import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from app.cli.series_register import main as register_main
from app.cli.series_show import main as show_main
from app.creative import EducationalCreativeBrief
from app.production.storyboard_video_planner import StoryboardVideoPlanner
from app.providers.openai_storyboard_provider import OpenAIStoryboardGenerator, _input
from app.series import (ConflictingSeriesBibleError, CorruptedSeriesBibleError, SeriesBible,
    SeriesNotFoundError, SeriesRegistry)
from app.storyboard import (DeterministicStoryboardGenerator, EpisodeService, StoryboardGenerationService,
    StoryboardSeriesContinuityError, StoryboardGenerationError, CreativeStoryboard)
from app.characters import CanonicalCharacterProfile,CharacterRegistry


ROOT = Path(__file__).parents[1]


def bible_payload():
    return json.loads((ROOT / "examples/smoke/luca-si-max-series-bible.json").read_text(encoding="utf-8"))


def brief_payload():
    return json.loads((ROOT / "examples/official/luca-si-max/episode-001-colors-brief.json").read_text(encoding="utf-8"))

def profiles():
    root=ROOT/"examples/official/luca-si-max/characters"
    return tuple(CanonicalCharacterProfile.model_validate_json((root/f"{name}.json").read_text(encoding="utf-8")) for name in ("luca","max"))

def setup_registries(root):
    characters=CharacterRegistry(Path(root)/"characters")
    for value in profiles(): characters.register(value)
    series=SeriesRegistry(Path(root)/"series",characters); series.register(SeriesBible.model_validate(bible_payload()))
    return series,characters


class _Response:
    def __init__(self, output): self.output_parsed = output


class _Responses:
    def __init__(self, output): self.output = output; self.kwargs = None
    def parse(self, **kwargs): self.kwargs = kwargs; return _Response(self.output)


class _Client:
    def __init__(self, output): self.responses = _Responses(output)


class _Registry:
    def __init__(self, bible): self.bible = bible
    def load(self, series_id): return self.bible

class _ProfileRegistry:
    def require_many(self,character_ids): return profiles()


class SeriesBibleTests(unittest.TestCase):
    def test_valid_contract_and_unknown_fields_rejected(self):
        bible = SeriesBible.model_validate(bible_payload())
        self.assertEqual(("luca", "max"), bible.resolved_character_ids)
        with self.assertRaises(ValidationError): SeriesBible.model_validate({**bible_payload(), "provider": "kling"})

    def test_duplicate_id_rejected(self):
        payload = bible_payload(); payload["required_character_ids"] = ["luca","luca"]
        with self.assertRaises(ValidationError): SeriesBible.model_validate(payload)

    def test_atomic_idempotent_and_conflicting_registration(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry = SeriesRegistry(Path(temporary)); bible = SeriesBible.model_validate(bible_payload())
            path = registry.register(bible)
            self.assertEqual(path, registry.register(bible)); self.assertFalse(path.with_suffix(".json.part").exists())
            changed = bible.model_copy(update={"visual_style": "Different original visual style"})
            with self.assertRaises(ConflictingSeriesBibleError): registry.register(changed)

    def test_lookup_missing_and_corrupted(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry = SeriesRegistry(Path(temporary))
            with self.assertRaises(SeriesNotFoundError): registry.load("luca-si-max")
            path = registry.path_for("luca-si-max"); path.parent.mkdir(parents=True); path.write_text("{", encoding="utf-8")
            with self.assertRaises(CorruptedSeriesBibleError): registry.load("luca-si-max")

    def test_brief_without_series_is_compatible(self):
        payload = brief_payload(); payload.pop("series_id")
        self.assertIsNone(EducationalCreativeBrief.model_validate(payload).series_id)

    def test_deterministic_storyboard_contains_canonical_characters_everywhere(self):
        bible = SeriesBible.model_validate(bible_payload()); brief = EducationalCreativeBrief.model_validate(brief_payload())
        with tempfile.TemporaryDirectory() as temporary:
            registry,characters=setup_registries(temporary)
            service = StoryboardGenerationService(DeterministicStoryboardGenerator(), registry,characters)
            first = service.generate(brief); second = service.generate(brief)
        self.assertEqual(first, second); self.assertEqual("luca-si-max", first.series_id)
        self.assertTrue(all(section.characters == ("luca", "max") for section in first.sections))
        self.assertEqual((),first.canonical_characters)

    def test_missing_character_and_invalid_section_character_rejected(self):
        bible = SeriesBible.model_validate(bible_payload()); brief = EducationalCreativeBrief.model_validate(brief_payload())
        storyboard = DeterministicStoryboardGenerator().generate_storyboard(brief, bible,profiles())
        class Generator:
            def __init__(self, value): self.value = value
            def generate_storyboard(self, brief, series_bible=None,character_profiles=()): return self.value
        missing_section=storyboard.sections[0].model_copy(update={"characters":("luca",)})
        missing=storyboard.model_copy(update={"sections":(missing_section,)+storyboard.sections[1:]})
        with self.assertRaises(StoryboardGenerationError):
            StoryboardGenerationService(Generator(missing), _Registry(bible),_ProfileRegistry()).generate(brief)
        unknown=storyboard.sections[0].model_copy(update={"characters":("luca","unknown")})
        with self.assertRaises(StoryboardGenerationError):
            StoryboardGenerationService(Generator(storyboard.model_copy(update={"sections":(unknown,)+storyboard.sections[1:]})),_Registry(bible),_ProfileRegistry()).generate(brief)

    def test_series_language_must_match_brief(self):
        bible = SeriesBible.model_validate(bible_payload()).model_copy(update={"language": "en"})
        with self.assertRaises(StoryboardSeriesContinuityError):
            StoryboardGenerationService(DeterministicStoryboardGenerator(), _Registry(bible),_ProfileRegistry()).generate(
                EducationalCreativeBrief.model_validate(brief_payload()))

    def test_openai_mapping_and_prompt_continuity_without_persistence(self):
        bible = SeriesBible.model_validate(bible_payload()); brief = EducationalCreativeBrief.model_validate(brief_payload())
        canonical=profiles(); storyboard = DeterministicStoryboardGenerator().generate_storyboard(brief, bible,canonical)
        client = _Client(storyboard); result = OpenAIStoryboardGenerator(client=client).generate_storyboard(brief, bible,canonical)
        self.assertEqual(storyboard, result)
        prompt = json.dumps(_input(brief, bible,canonical), ensure_ascii=False)
        self.assertIn("Max never speaks", prompt); self.assertIn("light blue T-shirt", prompt)
        durable = storyboard.model_dump_json()
        self.assertNotIn("provider", durable.casefold()); self.assertNotIn("prompt", durable.casefold())

    def test_mutations_and_non_speaking_violation_rejected(self):
        bible = SeriesBible.model_validate(bible_payload()); brief = EducationalCreativeBrief.model_validate(brief_payload())
        original = DeterministicStoryboardGenerator().generate_storyboard(brief, bible,profiles())
        class Generator:
            def __init__(self, value): self.value = value
            def generate_storyboard(self, brief, series_bible=None,character_profiles=()): return self.value
        section = original.sections[0].model_copy(update={"lyrics": "Max says hello."})
        with self.assertRaises(StoryboardSeriesContinuityError):
            StoryboardGenerationService(Generator(original.model_copy(update={"sections": (section,) + original.sections[1:]})), _Registry(bible),_ProfileRegistry()).generate(brief)

    def test_episode_and_video_preserve_canonical_identity(self):
        bible = SeriesBible.model_validate(bible_payload()); brief = EducationalCreativeBrief.model_validate(brief_payload())
        storyboard = DeterministicStoryboardGenerator().generate_storyboard(brief, bible,profiles())
        with tempfile.TemporaryDirectory() as temporary:
            series,characters=setup_registries(temporary)
            episode = EpisodeService(character_registry=characters,series_registry=series).resolve(storyboard)
            requests = StoryboardVideoPlanner(character_registry=characters,series_registry=series).build(storyboard, "production-1")
        self.assertEqual(["luca", "max"], [value.id for value in episode.characters])
        self.assertIn("blue T-shirt", episode.characters[0].appearance)
        for request in requests:
            appearances = {value.id: value.appearance for value in request.video_request.characters}
            self.assertIn("light blue T-shirt", appearances["luca"]); self.assertIn("German Shepherd", appearances["max"])
            self.assertIn("red collar", appearances["max"])

    def test_cli_registration_and_show(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "series"; character_root=Path(temporary)/"characters"; fixture = ROOT / "examples/smoke/luca-si-max-series-bible.json"
            registry=CharacterRegistry(character_root)
            for value in profiles(): registry.register(value)
            output = io.StringIO()
            with patch.object(sys, "argv", ["series_register", "--input", str(fixture), "--runtime-root", str(root),"--character-runtime-root",str(character_root)]), redirect_stdout(output):
                self.assertEqual(0, register_main())
            self.assertIn("Registration: succeeded", output.getvalue())
            output = io.StringIO()
            with patch.object(sys, "argv", ["series_show", "--series-id", "luca-si-max", "--runtime-root", str(root)]), redirect_stdout(output):
                self.assertEqual(0, show_main())
            self.assertIn("max", output.getvalue())

    def test_official_fixture_validates(self):
        brief = EducationalCreativeBrief.model_validate(brief_payload())
        self.assertEqual("luca-si-max", brief.series_id)


if __name__ == "__main__": unittest.main()
