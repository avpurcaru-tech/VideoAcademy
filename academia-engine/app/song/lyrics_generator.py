from __future__ import annotations

from typing import Protocol, runtime_checkable

from .contracts import EducationalSongBrief, LyricsLine, LyricsPlan, LyricsSection


@runtime_checkable
class LyricsGenerator(Protocol):
    """Provider-neutral structural contract for one lyrics generator."""

    def generate_lyrics(self, brief: EducationalSongBrief) -> LyricsPlan: ...


class DeterministicLyricsGenerator:
    """Simple repeatable local generator for tests and development; it is not AI."""

    def generate_lyrics(self, brief: EducationalSongBrief) -> LyricsPlan:
        if brief.language.casefold() == "ro":
            title=f"Cântec despre {brief.topic}"
            verse=(f"Învățăm astăzi despre {brief.topic}.",
                   f"Descoperim împreună: {brief.learning_objectives[0]}.")
            chorus=(f"Hai să învățăm {brief.topic}!", "Repetăm voioși și ne amintim!")
        else:
            title=f"Learning about {brief.topic}"
            verse=(f"Today we learn about {brief.topic}.",
                   f"Together we practice: {brief.learning_objectives[0]}.")
            chorus=(f"Let us learn about {brief.topic}!", "We repeat together and remember!")
        return LyricsPlan(song_id=brief.song_id,title=title,language=brief.language,sections=(
            LyricsSection(section_id="verse-0001",kind="verse",order=0,
                          lines=tuple(LyricsLine(line_id=f"line-{index:04d}",text=text)
                                      for index,text in enumerate(verse,start=1))),
            LyricsSection(section_id="chorus-0001",kind="chorus",order=1,
                          lines=tuple(LyricsLine(line_id=f"line-{index:04d}",text=text)
                                      for index,text in enumerate(chorus,start=3))),
        ))


class LyricsGeneratorRegistryError(RuntimeError): pass
class UnsupportedLyricsGeneratorError(LyricsGeneratorRegistryError): pass


class LyricsGeneratorRegistry:
    def __init__(self, generators: dict[str,LyricsGenerator] | None=None) -> None:
        self._default_registry=generators is None
        self._generators=dict(generators) if generators is not None else {"deterministic":DeterministicLyricsGenerator()}

    def resolve(self, name: str) -> LyricsGenerator:
        generator=self._generators.get(name)
        if generator is None and name=="openai" and self._default_registry:
            try:
                from app.providers.openai_lyrics_provider import OpenAILyricsGenerator
                generator=OpenAILyricsGenerator()
            except Exception as error:
                raise LyricsGeneratorRegistryError("Lyrics generator could not be configured.") from error
        if generator is None or not isinstance(generator,LyricsGenerator):
            raise UnsupportedLyricsGeneratorError("Lyrics generator is unsupported.")
        return generator
