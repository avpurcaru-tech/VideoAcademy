from .contracts import EducationalSongBrief, LyricsPlan, MusicPlan, ResolvedLyricsPlan, SongProductionPlan


class SongPlanner:
    """Validate and combine semantic song components without generating content."""

    def plan(self, brief: EducationalSongBrief, lyrics: LyricsPlan, music: MusicPlan) -> SongProductionPlan:
        return SongProductionPlan(brief=brief, lyrics=lyrics, music=music)


def resolve_lyrics(lyrics: LyricsPlan) -> ResolvedLyricsPlan:
    sections=tuple(sorted(lyrics.sections,key=lambda section: section.order))
    return ResolvedLyricsPlan(song_id=lyrics.song_id,title=lyrics.title,language=lyrics.language,
                              sections=sections,structural_order=tuple(section.section_id for section in sections))

