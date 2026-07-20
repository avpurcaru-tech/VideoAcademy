from pydantic import BaseModel, ValidationError

from .contracts import EducationalSongBrief, LyricsPlan
from .lyrics_generator import LyricsGenerator


class LyricsGenerationError(RuntimeError): pass
class LyricsGeneratorFailureError(LyricsGenerationError): pass
class InvalidGeneratedLyricsError(LyricsGenerationError): pass
class GeneratedLyricsSongIdMismatchError(LyricsGenerationError): pass
class GeneratedLyricsLanguageMismatchError(LyricsGenerationError): pass


class LyricsGenerationService:
    def __init__(self, generator: LyricsGenerator) -> None:
        if not isinstance(generator,LyricsGenerator):
            raise LyricsGeneratorFailureError("Lyrics generator does not satisfy the provider-neutral contract.")
        self._generator=generator

    def generate(self, brief: EducationalSongBrief) -> LyricsPlan:
        try: validated_brief=EducationalSongBrief.model_validate(_payload(brief))
        except ValidationError as error: raise InvalidGeneratedLyricsError("Educational song brief is invalid.") from error
        try: generated=self._generator.generate_lyrics(validated_brief)
        except Exception as error: raise LyricsGeneratorFailureError("Lyrics generator failed at a safe boundary.") from error
        try: lyrics=LyricsPlan.model_validate(_payload(generated))
        except (ValidationError,TypeError) as error: raise InvalidGeneratedLyricsError("Generated lyrics are invalid.") from error
        if lyrics.song_id != validated_brief.song_id:
            raise GeneratedLyricsSongIdMismatchError("Generated lyrics song ID does not match the brief.")
        if lyrics.language != validated_brief.language:
            raise GeneratedLyricsLanguageMismatchError("Generated lyrics language does not match the brief.")
        return lyrics


def _payload(value):
    return value.model_dump(mode="python") if isinstance(value,BaseModel) else value

