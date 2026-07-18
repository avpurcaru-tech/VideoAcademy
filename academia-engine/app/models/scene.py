from pydantic import BaseModel, Field

from .camera import Camera
from .location import Location
from .music import Music


class Scene(BaseModel):
    number: int = Field(ge=1)
    narration: str = Field(min_length=1, max_length=2000)
    visual_description: str = Field(min_length=1, max_length=2000)
    duration_seconds: int = Field(ge=1, le=300)
    character_ids: list[str] = Field(default_factory=list)
    location: Location
    camera: Camera
    music: Music | None = None
