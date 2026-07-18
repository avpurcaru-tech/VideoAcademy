from pathlib import Path

from app.models import Episode

from .contracts import EpisodeWriter, StoryGenerator
from .json_writer import JsonEpisodeWriter
from .request import StoryRequest


class StoryEngine:
    def __init__(self, generator: StoryGenerator, writer: EpisodeWriter | None = None) -> None:
        self._generator = generator
        self._writer = writer or JsonEpisodeWriter()

    def create_episode(self, request: StoryRequest, output_path: Path) -> Episode:
        episode = self._generator.generate(request)
        self._writer.write(episode, output_path)
        return episode
