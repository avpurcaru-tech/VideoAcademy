from pydantic import BaseModel,ValidationError

from app.models import Episode

from .contracts import EducationalCreativeBrief
from .episode_generator import EpisodeGenerator


class CreativeEpisodeError(RuntimeError): pass
class EpisodeGeneratorFailureError(CreativeEpisodeError): pass
class InvalidGeneratedEpisodeError(CreativeEpisodeError): pass
class GeneratedEpisodeSceneCountError(CreativeEpisodeError): pass
class GeneratedEpisodeSceneOrderError(CreativeEpisodeError): pass
class GeneratedEpisodeLanguageError(CreativeEpisodeError): pass
class GeneratedEpisodeIdentityError(CreativeEpisodeError): pass


class EpisodeGenerationService:
    def __init__(self,generator: EpisodeGenerator,duration_policy=None):
        if not isinstance(generator,EpisodeGenerator): raise EpisodeGeneratorFailureError("Episode generator is unavailable.")
        self._generator=generator
        self._duration_policy=duration_policy
    def generate(self,brief):
        from app.storyboard import CreativeStoryboard, EpisodeService
        if isinstance(brief,(Episode,CreativeStoryboard)):
            return EpisodeService().resolve(brief)
        try: brief=EducationalCreativeBrief.model_validate(_payload(brief))
        except ValidationError as error: raise InvalidGeneratedEpisodeError("Creative brief is invalid.") from error
        planned_brief=brief
        if self._duration_policy is not None:
            planned_brief=brief.model_copy(update={"scene_count":self._duration_policy.scene_count(brief.target_duration_seconds)})
        try: generated=self._generator.generate_episode(planned_brief)
        except Exception as error: raise EpisodeGeneratorFailureError("Episode generator failed at a safe boundary.") from error
        try: episode=Episode.model_validate(_payload(generated)).model_copy(update={"id":brief.brief_id})
        except (ValidationError,TypeError) as error: raise InvalidGeneratedEpisodeError("Generated Episode is invalid.") from error
        if len(episode.scenes)!=planned_brief.scene_count: raise GeneratedEpisodeSceneCountError("Generated Episode scene count differs from the duration plan.")
        numbers=[scene.number for scene in episode.scenes]
        if len(numbers)!=len(set(numbers)) or numbers!=list(range(1,planned_brief.scene_count+1)):
            raise GeneratedEpisodeSceneOrderError("Generated Episode scene numbering must be unique and contiguous.")
        if episode.metadata.language!=brief.language: raise GeneratedEpisodeLanguageError("Generated Episode language differs from the brief.")
        if episode.metadata.target_age_min!=brief.target_age_min or episode.metadata.target_age_max!=brief.target_age_max:
            raise GeneratedEpisodeLanguageError("Generated Episode target age differs from the brief.")
        known={character.id for character in episode.characters}
        if episode.scenes and (not known or not set.intersection(*(set(scene.character_ids) for scene in episode.scenes))):
            raise InvalidGeneratedEpisodeError("Generated Episode lacks a stable recurring character.")
        if any(not set(scene.character_ids)<=known for scene in episode.scenes):
            raise InvalidGeneratedEpisodeError("Generated Episode references an unknown character.")
        return episode


def _payload(value): return value.model_dump(mode="python") if isinstance(value,BaseModel) else value
