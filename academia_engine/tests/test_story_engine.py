from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.engines.story.engine import StoryEngine
from src.models import Episode, EpisodeMetadata, StoryboardScene


class FakeStoryModel:
    def generate(self, topic: str) -> Episode:
        return Episode(
            title=f"Descoperim {topic}",
            lyrics="Hai să învățăm împreună!",
            storyboard=[
                StoryboardScene(
                    scene_number=1,
                    narration="Începem aventura.",
                    visual_description="Un soare zâmbitor apare.",
                    duration_seconds=5,
                )
            ],
            metadata=EpisodeMetadata(
                topic=topic,
                age_group="4-7",
                language="ro",
                tags=[topic],
                estimated_duration_seconds=5,
            ),
        )


class StoryEngineTests(unittest.TestCase):
    def test_story_engine_writes_episode_json(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "episode.json"

            episode = StoryEngine(FakeStoryModel()).create_episode("planete", output_path)

            self.assertEqual(episode.metadata.topic, "planete")
            self.assertEqual(
                json.loads(output_path.read_text(encoding="utf-8"))["title"],
                "Descoperim planete",
            )
