from pydantic import BaseModel, Field

from .character import Character
from .metadata import Metadata
from .scene import Scene


class Episode(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    title: str = Field(min_length=1, max_length=200)
    lyrics: str = Field(min_length=1, max_length=10000)
    metadata: Metadata
    characters: list[Character] = Field(default_factory=list)
    scenes: list[Scene] = Field(min_length=1)
