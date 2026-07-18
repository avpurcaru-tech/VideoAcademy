from pydantic import BaseModel, Field

from app.models import Character


class StoryRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=200)
    language: str = Field(pattern=r"^[a-z]{2}(?:-[A-Z]{2})?$")
    duration_seconds: int = Field(ge=30, le=1800)
    characters: list[Character] = Field(min_length=1)
