import re

from pydantic import BaseModel, ValidationError

from app.creative import EducationalCreativeBrief

from .contracts import CreativeStoryboard
from .generator import StoryboardGenerator


class StoryboardGenerationError(RuntimeError): pass
class InvalidStoryboardError(StoryboardGenerationError): pass
class StoryboardGeneratorFailureError(StoryboardGenerationError): pass
class StoryboardLanguageMismatchError(StoryboardGenerationError): pass
class StoryboardAudienceMismatchError(StoryboardGenerationError): pass
class StoryboardIdentityMismatchError(StoryboardGenerationError): pass
class StoryboardSeriesContinuityError(StoryboardGenerationError): pass

def _diagnostic(error,category,path,message):
    error.failure_category=category; error.failure_details=(f"{path}: {message}",); return error


class StoryboardGenerationService:
    def __init__(self, generator: StoryboardGenerator, series_registry=None, character_registry=None):
        if not isinstance(generator, StoryboardGenerator):
            raise StoryboardGeneratorFailureError("Storyboard generator is unavailable.")
        self._generator = generator
        self._series_registry = series_registry
        self._character_registry = character_registry

    def generate(self, brief):
        try:
            brief = EducationalCreativeBrief.model_validate(_payload(brief))
        except ValidationError as error:
            raise InvalidStoryboardError("Creative brief is invalid.") from error
        series_bible = None
        character_profiles = ()
        if brief.series_id:
            from app.series import SeriesRegistry
            series_bible = (self._series_registry or SeriesRegistry()).load(brief.series_id)
            if series_bible.language != brief.language:
                raise StoryboardSeriesContinuityError("Creative brief language differs from the registered series.")
            from app.characters import CharacterRegistry
            character_profiles=(self._character_registry or CharacterRegistry()).require_many(series_bible.resolved_character_ids)
        try:
            generated = (self._generator.generate_storyboard(brief, series_bible, character_profiles) if series_bible is not None
                else self._generator.generate_storyboard(brief))
        except Exception as error:
            wrapped=StoryboardGeneratorFailureError("Storyboard generator failed at a safe boundary.")
            wrapped.provider_error=error
            raise wrapped from error
        try:
            storyboard = CreativeStoryboard.model_validate(_payload(generated))
        except (ValidationError, TypeError) as error:
            raise InvalidStoryboardError("Generated storyboard is invalid.") from error
        if storyboard.storyboard_id != brief.brief_id:
            raise StoryboardIdentityMismatchError("Storyboard identity differs from the brief.")
        if storyboard.language != brief.language:
            raise StoryboardLanguageMismatchError("Storyboard language differs from the brief.")
        expected = (brief.target_age_min, brief.target_age_max)
        actual = (storyboard.audience.target_age_min, storyboard.audience.target_age_max)
        if actual != expected:
            raise StoryboardAudienceMismatchError("Storyboard audience differs from the brief.")
        if abs(storyboard.target_duration_seconds - brief.target_duration_seconds) > 0.01:
            raise _diagnostic(InvalidStoryboardError("Storyboard duration differs from the brief."),
                "storyboard_duration_mismatch","target_duration_seconds","duration differs from creative brief")
        if len(storyboard.sections) != brief.scene_count:
            raise _diagnostic(InvalidStoryboardError("Storyboard section count differs from the brief."),
                "storyboard_scene_count_mismatch","sections",f"expected {brief.scene_count}, received {len(storyboard.sections)}")
        if series_bible is not None:
            self._validate_series(storyboard, series_bible, character_profiles)
        return storyboard

    @staticmethod
    def _validate_series(storyboard, bible, profiles):
        if storyboard.series_id != bible.series_id:
            raise StoryboardSeriesContinuityError("Storyboard series identity differs from the registered series.")
        canonical={value.character_id:value for value in storyboard.canonical_characters}
        unknown_canonical=set(canonical)-set(bible.resolved_character_ids)
        if unknown_canonical:
            value=sorted(unknown_canonical)[0]
            raise _diagnostic(StoryboardSeriesContinuityError("Storyboard redefines canonical identity."),
                "storyboard_unknown_character","canonical_characters",f"unknown character {value}")
        bible_characters={value.character_id:value for value in bible.characters}
        for character_id,value in canonical.items():
            expected=bible_characters[character_id]
            if value.name != expected.name:
                raise _diagnostic(StoryboardSeriesContinuityError("Storyboard renames a canonical character."),
                    "storyboard_character_identity_redefined",f"canonical_characters.{character_id}.name","canonical name changed")
            if value.appearance != expected.appearance:
                raise _diagnostic(StoryboardSeriesContinuityError("Storyboard changes canonical appearance."),
                    "storyboard_character_identity_redefined",f"canonical_characters.{character_id}.appearance","canonical appearance changed")
            if value.clothing != expected.clothing:
                raise _diagnostic(StoryboardSeriesContinuityError("Storyboard changes canonical clothing."),
                    "storyboard_character_clothing_changed",f"canonical_characters.{character_id}.clothing","canonical clothing changed")
        required_ids = set(bible.resolved_character_ids)
        for section in storyboard.sections:
            references = set(section.characters)
            missing=required_ids-references; unknown=references-required_ids
            if missing:
                value=sorted(missing)[0]
                raise _diagnostic(StoryboardSeriesContinuityError("Storyboard is missing a required character."),
                    "storyboard_missing_required_character",f"sections.{section.order}.characters",f"missing required character {value}")
            if unknown:
                value=sorted(unknown)[0]
                raise _diagnostic(StoryboardSeriesContinuityError("Storyboard references an unknown character."),
                    "storyboard_unknown_character",f"sections.{section.order}.characters",f"unknown character {value}")
            for source in profiles:
                if any("never speaks" in rule.casefold() or "does not speak" in rule.casefold() for rule in (*source.behavior_rules,*source.negative_rules)):
                    escaped = re.escape(source.name)
                    if re.search(rf"\b{escaped}\b\s*(?::|(?:says|said|speaks|spune|zice)\b)", section.lyrics, re.IGNORECASE):
                        category="storyboard_max_speaks" if source.character_id=="max" else "storyboard_character_behavior_violation"
                        raise _diagnostic(StoryboardSeriesContinuityError("Storyboard violates canonical behavior."),category,
                            f"sections.{section.order}.dialogue",f"{source.name} must not speak")


def _payload(value):
    return value.model_dump(mode="python") if isinstance(value, BaseModel) else value
