import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.engines.story import StoryEngine, StoryRequest
from app.models import Character, Episode, Metadata, Scene
from app.models.camera import Camera
from app.models.location import Location


class FakeStoryGenerator:
    def generate(self, request: StoryRequest) -> Episode:
        return Episode(
            id="episode-space",
            title="Aventura spațiului",
            lyrics="Să explorăm împreună!",
            metadata=Metadata(
                topic=request.topic,
                language=request.language,
                target_age_min=4,
                target_age_max=7,
                tags=["space"],
            ),
            characters=request.characters,
            scenes=[
                Scene(
                    number=1,
                    narration="Începe aventura.",
                    visual_description="Stele colorate.",
                    duration_seconds=request.duration_seconds,
                    character_ids=[request.characters[0].id],
                    location=Location(
                        name="Spațiu",
                        description="Cer plin de stele.",
                        time_of_day="noapte",
                    ),
                    camera=Camera(
                        shot_type="wide",
                        description="Cadru larg.",
                    ),
                )
            ],
        )


class StoryEngineTests(unittest.TestCase):
    def test_story_engine_writes_episode_json(self) -> None:
        request = StoryRequest(
            topic="Spațiu",
            language="ro",
            duration_seconds=60,
            characters=[
                Character(
                    id="mia",
                    name="Mia",
                    role="explorator",
                    description="O copilă curioasă.",
                    appearance="Costum spațial mov.",
                )
            ],
        )
        with TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "episode.json"

            StoryEngine(FakeStoryGenerator()).create_episode(request, output_path)

            self.assertEqual(
                json.loads(output_path.read_text(encoding="utf-8"))["metadata"]["topic"],
                "Spațiu",
            )
