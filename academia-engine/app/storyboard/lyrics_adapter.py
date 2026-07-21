from app.song.contracts import LyricsLine, LyricsPlan, LyricsSection, LyricsSectionKind

from .contracts import CreativeStoryboard


class StoryboardLyricsAdapterError(RuntimeError): pass


class StoryboardLyricsAdapter:
    """Pure deterministic projection of storyboard lyrics into the legacy lyrics contract."""
    def adapt(self, storyboard: CreativeStoryboard) -> LyricsPlan:
        try:
            storyboard = CreativeStoryboard.model_validate(storyboard)
            if len(storyboard.sections) < 2:
                raise StoryboardLyricsAdapterError("Storyboard requires at least two sections for educational lyrics.")
            sections = tuple(LyricsSection(section_id=section.section_id,
                kind=self._kind(section.section_type, index), order=section.order,
                lines=(LyricsLine(line_id=f"{section.section_id}-line-01", text=section.lyrics),))
                for index, section in enumerate(storyboard.sections))
            return LyricsPlan(song_id=storyboard.storyboard_id, title=storyboard.title,
                language=storyboard.language, sections=sections)
        except StoryboardLyricsAdapterError:
            raise
        except Exception as error:
            raise StoryboardLyricsAdapterError("Storyboard could not be projected to lyrics.") from error

    @staticmethod
    def _kind(section_type: str, index: int) -> LyricsSectionKind:
        if index == 0:
            return LyricsSectionKind.VERSE
        if index == 1:
            return LyricsSectionKind.CHORUS
        try:
            return LyricsSectionKind(section_type.lower())
        except ValueError:
            return LyricsSectionKind.VERSE
