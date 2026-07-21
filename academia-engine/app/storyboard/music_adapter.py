from app.music.contracts import MusicGenerationRequest
from app.song.contracts import MusicPlan

from .contracts import CreativeStoryboard
from .lyrics_adapter import StoryboardLyricsAdapter


class StoryboardMusicAdapterError(RuntimeError): pass


class StoryboardMusicAdapter:
    """Pure deterministic projection of authoritative storyboard music semantics."""
    def music_plan(self, storyboard: CreativeStoryboard) -> MusicPlan:
        try:
            storyboard = CreativeStoryboard.model_validate(storyboard)
            direction = storyboard.music_direction
            return MusicPlan(song_id=storyboard.storyboard_id, tempo_bpm=direction.tempo_bpm,
                musical_style=direction.style, mood=direction.mood,
                instrumentation=direction.instrumentation, vocal_style=direction.vocals,
                target_duration_seconds=storyboard.target_duration_seconds)
        except Exception as error:
            raise StoryboardMusicAdapterError("Storyboard could not be projected to a music plan.") from error

    def adapt(self, storyboard: CreativeStoryboard) -> MusicGenerationRequest:
        try:
            storyboard = CreativeStoryboard.model_validate(storyboard)
            lyrics = StoryboardLyricsAdapter().adapt(storyboard)
            return MusicGenerationRequest(song_id=storyboard.storyboard_id, title=storyboard.title,
                lyrics=lyrics, music_plan=self.music_plan(storyboard))
        except StoryboardMusicAdapterError:
            raise
        except Exception as error:
            raise StoryboardMusicAdapterError("Storyboard could not be projected to a music request.") from error
