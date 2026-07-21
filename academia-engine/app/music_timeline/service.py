from app.song import LyricsPlan
from app.storyboard import CreativeStoryboard

from .contracts import MusicTimeline
from .generator import MusicTimelineGenerator


class MusicTimelineGenerationError(RuntimeError): pass
class MusicTimelineGeneratorFailureError(MusicTimelineGenerationError): pass
class InvalidMusicTimelineError(MusicTimelineGenerationError): pass


class MusicTimelineGenerationService:
    def __init__(self, generator: MusicTimelineGenerator):
        if not isinstance(generator, MusicTimelineGenerator):
            raise MusicTimelineGeneratorFailureError("Music timeline generator is unavailable.")
        self._generator = generator

    def generate(self, storyboard, lyrics, music_duration_seconds):
        try:
            storyboard = CreativeStoryboard.model_validate(storyboard)
            lyrics = LyricsPlan.model_validate(lyrics)
            duration = float(music_duration_seconds)
            if duration <= 0: raise ValueError
        except Exception as error:
            raise InvalidMusicTimelineError("Music timeline inputs are invalid.") from error
        if lyrics.song_id != storyboard.storyboard_id or lyrics.language != storyboard.language:
            raise InvalidMusicTimelineError("Storyboard and lyrics identities are inconsistent.")
        try:
            generated = self._generator.generate_timeline(storyboard, lyrics, duration)
        except Exception as error:
            raise MusicTimelineGeneratorFailureError("Music timeline generator failed safely.") from error
        try:
            timeline = MusicTimeline.model_validate(generated)
        except Exception as error:
            raise InvalidMusicTimelineError("Generated music timeline is invalid.") from error
        expected = tuple(section.section_id for section in storyboard.sections)
        actual = tuple(segment.storyboard_section_id for segment in timeline.segments)
        if timeline.storyboard_id != storyboard.storyboard_id or actual != expected:
            raise InvalidMusicTimelineError("Music timeline does not preserve storyboard section ordering.")
        if abs(timeline.music_duration_seconds - duration) > 0.01:
            raise InvalidMusicTimelineError("Music timeline duration differs from measured audio duration.")
        return timeline
