from pydantic import BaseModel, ConfigDict, Field

from .camera import Camera
from .character import Character
from .character_action import CharacterAction
from .lighting import Lighting
from .location import Location
from .transition import Transition


class DirectorScene(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_number: int = Field(ge=1)
    duration_seconds: int = Field(ge=1, le=300)
    location: Location
    characters: list[Character] = Field(default_factory=list)
    character_actions: list[CharacterAction] = Field(default_factory=list)
    camera: Camera
    lighting: Lighting
    transition: Transition


class DirectorPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episode_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    episode_title: str = Field(min_length=1, max_length=200)
    scenes: list[DirectorScene] = Field(min_length=1)
