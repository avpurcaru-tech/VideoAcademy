from __future__ import annotations

from pathlib import Path
from typing import Protocol

from src.models import Episode


class StoryModel(Protocol):
    def generate(self, topic: str) -> Episode:
        """Return a validated episode for the requested topic."""


class EpisodeWriter(Protocol):
    def write(self, episode: Episode, destination: Path) -> None:
        """Persist an episode as JSON."""
