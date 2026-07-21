from typing import Protocol, runtime_checkable

from app.song import LyricsPlan
from app.storyboard import CreativeStoryboard

from .contracts import MusicTimeline


@runtime_checkable
class MusicTimelineGenerator(Protocol):
    def generate_timeline(self, storyboard: CreativeStoryboard, lyrics: LyricsPlan,
                          music_duration_seconds: float) -> MusicTimeline: ...
