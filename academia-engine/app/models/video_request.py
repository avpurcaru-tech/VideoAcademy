from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .camera import Camera
from .character_action import CharacterAction
from .transition import Transition


class VideoCharacter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=100)
    role: str = Field(min_length=1, max_length=100)
    appearance: str = Field(min_length=1, max_length=1000)


class VideoEnvironment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    location_name: str = Field(min_length=1, max_length=150)
    location_description: str = Field(min_length=1, max_length=1000)
    time_of_day: str = Field(min_length=1, max_length=100)
    lighting_description: str = Field(min_length=1, max_length=500)
    lighting_intensity: Literal["low", "medium", "high"]


class VideoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_number: int = Field(ge=1)
    duration_seconds: int = Field(ge=1, le=300)
    environment: VideoEnvironment
    characters: list[VideoCharacter] = Field(default_factory=list)
    character_actions: list[CharacterAction] = Field(default_factory=list)
    camera: Camera
    transition: Transition
