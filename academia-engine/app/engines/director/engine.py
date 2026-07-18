from app.models import Character, DirectorPlan, Episode, Scene

from .contracts import SceneDirector
from .semantic_director import SemanticDirector


class DirectorEngine:
    def __init__(self, scene_director: SceneDirector | None = None) -> None:
        self._scene_director = scene_director or SemanticDirector()

    def create_plan(self, episode: Episode) -> DirectorPlan:
        characters_by_id = {character.id: character for character in episode.characters}
        directed_scenes = []

        for index, scene in enumerate(episode.scenes):
            scene_characters = self._resolve_characters(scene, characters_by_id)
            directed_scenes.append(
                self._scene_director.direct(
                    scene=scene,
                    characters=scene_characters,
                    is_final_scene=index == len(episode.scenes) - 1,
                )
            )

        return DirectorPlan(
            episode_id=episode.id,
            episode_title=episode.title,
            scenes=directed_scenes,
        )

    @staticmethod
    def _resolve_characters(
        scene: Scene,
        characters_by_id: dict[str, Character],
    ) -> list[Character]:
        missing_character_ids = [
            character_id for character_id in scene.character_ids if character_id not in characters_by_id
        ]
        if missing_character_ids:
            missing_ids = ", ".join(missing_character_ids)
            raise ValueError(f"Unknown character IDs in scene: {missing_ids}")
        return [characters_by_id[character_id] for character_id in scene.character_ids]
