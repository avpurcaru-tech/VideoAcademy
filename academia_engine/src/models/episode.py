from __future__ import annotations

from pydantic import BaseModel, Field


class StoryboardScene(BaseModel):
    scene_number: int = Field(ge=1)
    narration: str = Field(min_length=1)
    visual_description: str = Field(min_length=1)
    duration_seconds: int = Field(ge=1)


class EpisodeMetadata(BaseModel):
    topic: str = Field(min_length=1)
    age_group: str = Field(min_length=1)
    language: str = Field(min_length=1)
    tags: list[str] = Field(min_length=1)
    estimated_duration_seconds: int = Field(ge=1)


class Episode(BaseModel):
    title: str = Field(min_length=1)
    lyrics: str = Field(min_length=1)
    storyboard: list[StoryboardScene] = Field(min_length=1)
    metadata: EpisodeMetadata
