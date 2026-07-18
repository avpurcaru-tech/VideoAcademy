from __future__ import annotations

from pathlib import Path

from src.engines.story.contracts import EpisodeWriter, StoryModel
from src.engines.story.json_writer import JsonEpisodeWriter
from src.models import Episode


class StoryEngine:
    """Generates and persists the episode contract from a topic."""

    def __init__(self, model: StoryModel, writer: EpisodeWriter | None = None) -> None:
        self._model = model
        self._writer = writer or JsonEpisodeWriter()

    def create_episode(self, topic: str, output_path: Path) -> Episode:
        normalized_topic = topic.strip()
        if not normalized_topic:
            raise ValueError("topic must not be empty")

        episode = self._model.generate(normalized_topic)
        self._writer.write(episode, output_path)
        return episode
