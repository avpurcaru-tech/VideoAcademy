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


class StoryboardGenerationService:
    def __init__(self, generator: StoryboardGenerator):
        if not isinstance(generator, StoryboardGenerator):
            raise StoryboardGeneratorFailureError("Storyboard generator is unavailable.")
        self._generator = generator

    def generate(self, brief):
        try:
            brief = EducationalCreativeBrief.model_validate(_payload(brief))
        except ValidationError as error:
            raise InvalidStoryboardError("Creative brief is invalid.") from error
        try:
            generated = self._generator.generate_storyboard(brief)
        except Exception as error:
            raise StoryboardGeneratorFailureError("Storyboard generator failed at a safe boundary.") from error
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
            raise InvalidStoryboardError("Storyboard duration differs from the brief.")
        return storyboard


def _payload(value):
    return value.model_dump(mode="python") if isinstance(value, BaseModel) else value
