import unittest

from app.models import (
    Camera,
    Character,
    CharacterAction,
    DirectorPlan,
    DirectorScene,
    Lighting,
    Location,
    Transition,
    VideoEnvironment,
    VideoRequest,
)
from app.prompts import PromptBuilder
from app.prompts.adapters import KlingPromptAdapter


class PromptBuilderTests(unittest.TestCase):
    def test_kling_adapter_creates_provider_neutral_video_request(self) -> None:
        requests = PromptBuilder(KlingPromptAdapter()).build(self._director_plan())

        request = requests[0]
        self.assertEqual(request.scene_number, 1)
        self.assertEqual(request.environment.location_name, "Spațiu")
        self.assertEqual(request.characters[0].id, "mia")
        self.assertEqual(request.character_actions[0].emotion, "curious")
        self.assertEqual(request.camera.shot_type, "wide")
        self.assertEqual(request.transition.type, "fade_to_black")

    def test_builder_accepts_another_adapter_without_changes(self) -> None:
        requests = PromptBuilder(FakePromptAdapter()).build(self._director_plan())

        self.assertEqual(requests[0].duration_seconds, 15)

    @staticmethod
    def _director_plan() -> DirectorPlan:
        return DirectorPlan(
            episode_id="space-episode",
            episode_title="Aventura spațiului",
            scenes=[
                DirectorScene(
                    scene_number=1,
                    duration_seconds=30,
                    location=Location(
                        name="Spațiu",
                        description="Cer plin de stele.",
                        time_of_day="noapte",
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
                    character_actions=[
                        CharacterAction(
                            character_id="mia",
                            action="Mia privește stelele.",
                            emotion="curious",
                        )
                    ],
                    camera=Camera(shot_type="wide", description="Cadru larg."),
                    lighting=Lighting(description="Lumină blândă de lună."),
                    transition=Transition(type="fade_to_black"),
                )
            ],
        )


class FakePromptAdapter:
    def create_video_request(self, scene: DirectorScene) -> VideoRequest:
        return VideoRequest(
            scene_number=scene.scene_number,
            duration_seconds=15,
            environment=VideoEnvironment(
                location_name=scene.location.name,
                location_description=scene.location.description,
                time_of_day=scene.location.time_of_day,
                lighting_description=scene.lighting.description,
                lighting_intensity=scene.lighting.intensity,
            ),
            camera=scene.camera,
            transition=scene.transition,
        )
