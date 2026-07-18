from typing import Protocol

from app.models import Character, DirectorScene, Scene


class SceneDirector(Protocol):
    def direct(self, scene: Scene, characters: list[Character], is_final_scene: bool) -> DirectorScene:
        """Create a provider-neutral semantic direction for one scene."""
