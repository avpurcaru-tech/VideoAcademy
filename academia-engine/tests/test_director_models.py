import unittest

from pydantic import ValidationError

from app.models import Camera, CharacterAction, DirectorPlan, DirectorScene, Lighting, Location, Transition


class DirectorPlanModelTests(unittest.TestCase):
    def test_director_plan_accepts_vendor_neutral_scene_direction(self) -> None:
        plan = DirectorPlan(
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
                    character_actions=[
                        CharacterAction(
                            character_id="mia",
                            action="Mia privește stelele.",
                            emotion="curious",
                        )
                    ],
                    camera=Camera(shot_type="wide", description="Cadru larg."),
                    lighting=Lighting(description="Lumină blândă de lună."),
                    transition=Transition(type="fade_to_black", duration_seconds=1),
                )
            ],
        )

        self.assertEqual(plan.scenes[0].camera.shot_type, "wide")

    def test_character_action_rejects_invalid_character_id(self) -> None:
        with self.assertRaises(ValidationError):
            CharacterAction(character_id="Mia", action="Privește stelele.", emotion="curious")

    def test_lighting_and_transition_validate_allowed_values(self) -> None:
        with self.assertRaises(ValidationError):
            Lighting(description="Lumină blândă.", intensity="very_high")

        with self.assertRaises(ValidationError):
            Transition(type="wipe")
