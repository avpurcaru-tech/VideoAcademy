from app.models import Character, CharacterAction, DirectorScene, Lighting, Scene, Transition


class SemanticDirector:
    """Creates a provider-neutral direction directly from an episode scene."""

    def direct(self, scene: Scene, characters: list[Character], is_final_scene: bool) -> DirectorScene:
        return DirectorScene(
            scene_number=scene.number,
            duration_seconds=scene.duration_seconds,
            location=scene.location,
            characters=characters,
            character_actions=[
                CharacterAction(
                    character_id=character.id,
                    action=scene.narration,
                    emotion="curious",
                )
                for character in characters
            ],
            camera=scene.camera,
            lighting=Lighting(description=self._lighting_for(scene.location.time_of_day)),
            transition=Transition(type="fade_to_black" if is_final_scene else "cut"),
        )

    @staticmethod
    def _lighting_for(time_of_day: str) -> str:
        normalized_time = time_of_day.casefold()
        if normalized_time in {"night", "noapte"}:
            return "soft moonlight with gentle highlights"
        if normalized_time in {"sunset", "apus"}:
            return "warm golden-hour light"
        return "soft, even daylight"
