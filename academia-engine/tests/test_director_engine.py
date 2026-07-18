import unittest

from app.engines.director import DirectorEngine
from app.models import Camera, Character, Episode, Location, Metadata, Scene


class DirectorEngineTests(unittest.TestCase):
    def test_create_plan_describes_every_scene(self) -> None:
        episode = Episode(
            id="space-episode",
            title="Aventura spațiului",
            lyrics="Să explorăm!",
            metadata=Metadata(
                topic="spațiu",
                language="ro",
                target_age_min=4,
                target_age_max=7,
                tags=["spațiu"],
            ),
            characters=[
                Character(
                    id="mia",
                    name="Mia",
                    role="explorator",
                    description="O copilă curioasă.",
                    appearance="Costum spațial mov.",
                )
            ],
            scenes=[
                Scene(
                    number=1,
                    narration="Mia privește stelele.",
                    visual_description="Stele colorate.",
                    duration_seconds=30,
                    character_ids=["mia"],
                    location=Location(
                        name="Spațiu",
                        description="Cer plin de stele.",
                        time_of_day="noapte",
                    ),
                    camera=Camera(shot_type="wide", description="Cadru larg."),
                )
            ],
        )

        plan = DirectorEngine().create_plan(episode)

        direction = plan.scenes[0]
        self.assertEqual(direction.duration_seconds, 30)
        self.assertEqual(direction.location.name, "Spațiu")
        self.assertEqual(direction.characters[0].id, "mia")
        self.assertEqual(direction.character_actions[0].action, "Mia privește stelele.")
        self.assertEqual(direction.character_actions[0].emotion, "curious")
        self.assertEqual(direction.camera.shot_type, "wide")
        self.assertEqual(direction.camera.movement, "static")
        self.assertEqual(direction.lighting.description, "soft moonlight with gentle highlights")
        self.assertEqual(direction.transition.type, "fade_to_black")

    def test_create_plan_rejects_unknown_scene_character(self) -> None:
        episode = Episode(
            id="space-episode",
            title="Aventura spațiului",
            lyrics="Să explorăm!",
            metadata=Metadata(
                topic="spațiu",
                language="ro",
                target_age_min=4,
                target_age_max=7,
                tags=["spațiu"],
            ),
            scenes=[
                Scene(
                    number=1,
                    narration="Mia privește stelele.",
                    visual_description="Stele colorate.",
                    duration_seconds=30,
                    character_ids=["mia"],
                    location=Location(
                        name="Spațiu",
                        description="Cer plin de stele.",
                        time_of_day="noapte",
                    ),
                    camera=Camera(shot_type="wide", description="Cadru larg."),
                )
            ],
        )

        with self.assertRaisesRegex(ValueError, "Unknown character IDs"):
            DirectorEngine().create_plan(episode)
