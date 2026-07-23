import io,json,sys,tempfile,unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from pydantic import ValidationError

from app.characters import (CanonicalCharacterProfile,CharacterNotFoundError,CharacterRegistry,
    ConflictingCharacterProfileError,CorruptedCharacterRecordError)
from app.cli.character_register import main as register_main
from app.cli.character_show import main as show_main
from app.creative import EducationalCreativeBrief
from app.production import StoryboardVideoPlanner
from app.providers.kling_mapper import KlingTextToVideoMapper,KlingCharacterReferenceUnsupportedError
from app.config import KlingGenerationSettings
from app.series import SeriesBible,SeriesRegistry
from app.storyboard import DeterministicStoryboardGenerator

ROOT=Path(__file__).parents[1]
CHARACTERS=ROOT/"examples/official/luca-si-max/characters"

def profile(name): return CanonicalCharacterProfile.model_validate_json((CHARACTERS/f"{name}.json").read_text(encoding="utf-8"))
def bible(): return SeriesBible.model_validate_json((ROOT/"examples/smoke/luca-si-max-series-bible.json").read_text(encoding="utf-8"))
def brief(): return EducationalCreativeBrief.model_validate_json((ROOT/"examples/official/luca-si-max/episode-001-colors-brief.json").read_text(encoding="utf-8"))

class CanonicalCharacterTests(unittest.TestCase):
    def test_official_profiles_are_valid_provider_neutral_and_exact(self):
        luca,max_profile=profile("luca"),profile("max")
        self.assertIn("soft golden-blond curly hair",luca.canonical_description)
        self.assertIn("German Shepherd",max_profile.canonical_description)
        fields=set(CanonicalCharacterProfile.model_fields)
        for forbidden in ("location","kling","provider","url","payload","credentials"): self.assertNotIn(forbidden,fields)

    def test_blank_description_unknown_fields_and_blank_rules_rejected(self):
        payload=json.loads((CHARACTERS/"luca.json").read_text(encoding="utf-8"))
        for update in ({"canonical_description":" "},{"behavior_rules":[""]},{"location":"park"}):
            with self.subTest(update=update),self.assertRaises(ValidationError): CanonicalCharacterProfile.model_validate({**payload,**update})

    def test_registry_atomic_idempotent_conflict_missing_and_corruption(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry=CharacterRegistry(Path(temporary)); luca=profile("luca"); path=registry.register(luca)
            self.assertEqual(path,registry.register(luca)); self.assertFalse(path.with_suffix(".json.part").exists())
            with self.assertRaises(ConflictingCharacterProfileError): registry.register(luca.model_copy(update={"name":"Other"}))
            with self.assertRaises(CharacterNotFoundError): registry.get("max")
            path.write_text("{",encoding="utf-8")
            with self.assertRaises(CorruptedCharacterRecordError): registry.get("luca")

    def test_series_registration_requires_profiles_when_dependency_is_enabled(self):
        with tempfile.TemporaryDirectory() as temporary:
            characters=CharacterRegistry(Path(temporary)/"characters")
            with self.assertRaises(CharacterNotFoundError): SeriesRegistry(Path(temporary)/"series",characters).register(bible())
            characters.register(profile("luca")); characters.register(profile("max"))
            self.assertTrue(SeriesRegistry(Path(temporary)/"series",characters).register(bible()).is_file())

    def test_video_prompt_contains_exact_deterministic_referenced_blocks_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            characters=CharacterRegistry(Path(temporary)/"characters")
            for value in (profile("luca"),profile("max")): characters.register(value)
            series=SeriesRegistry(Path(temporary)/"series",characters); series.register(bible())
            storyboard=DeterministicStoryboardGenerator().generate_storyboard(brief(),bible(),(profile("luca"),profile("max")))
            requests=StoryboardVideoPlanner(character_registry=characters,series_registry=series).build(storyboard,"video-1")
            luca_references=[next(reference for reference in request.character_reference_images
                if reference.character_id=="luca") for request in requests]
            max_references=[next(reference for reference in request.character_reference_images
                if reference.character_id=="max") for request in requests]
            self.assertEqual(1,len({(value.local_path,value.sha256) for value in luca_references}))
            self.assertEqual(1,len({(value.local_path,value.sha256) for value in max_references}))
            mapper=KlingTextToVideoMapper(KlingGenerationSettings())
            payloads=[mapper.prompt_with_diagnostic(value)[0] for value in requests]
            with self.assertRaises(KlingCharacterReferenceUnsupportedError):
                mapper.map(requests[0],external_task_id="external-0")
            luca_description=profile("luca").canonical_description; max_description=profile("max").canonical_description
            self.assertTrue(all("golden-blond" in value and "bright blue eyes" in value for value in payloads))
            self.assertTrue(all("German Shepherd" in value and "red collar" in value and "Max never speaks" in value for value in payloads))
            luca_blocks=[next(character.appearance for character in request.video_request.characters if character.id=="luca") for request in requests]
            self.assertEqual(1,len(set(luca_blocks)))
            section=storyboard.sections[0].model_copy(update={"characters":("luca",)})
            luca_only=storyboard.model_copy(update={"sections":(section,)})
            # Keep duration coherent for the one-section projection contract.
            luca_only=luca_only.model_copy(update={"target_duration_seconds":section.estimated_duration_seconds})
            request=StoryboardVideoPlanner(character_registry=characters,series_registry=series).build(luca_only,"video-2")[0]
            text=mapper.prompt_with_diagnostic(request)[0]
            self.assertIn("golden-blond",text); self.assertNotIn("German Shepherd",text)
            self.assertIn("sunny park",text.lower())

    def test_character_cli_register_and_show(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/"characters"; output=io.StringIO()
            with patch.object(sys,"argv",["character_register","--input",str(CHARACTERS/"luca.json"),"--runtime-root",str(root)]),redirect_stdout(output):
                self.assertEqual(0,register_main())
            self.assertIn("Registration: succeeded",output.getvalue()); output=io.StringIO()
            with patch.object(sys,"argv",["character_show","--character-id","luca","--runtime-root",str(root)]),redirect_stdout(output):
                self.assertEqual(0,show_main())
            self.assertIn(profile("luca").canonical_description,output.getvalue())

if __name__=="__main__": unittest.main()
