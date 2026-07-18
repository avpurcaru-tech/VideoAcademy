from pathlib import Path
from typing import Protocol

from app.models import Episode

from .request import StoryRequest


class StoryGenerator(Protocol):
    def generate(self, request: StoryRequest) -> Episode:
        """Generate an episode from a validated story request."""


class EpisodeWriter(Protocol):
    def write(self, episode: Episode, destination: Path) -> None:
        """Persist an episode as JSON."""
