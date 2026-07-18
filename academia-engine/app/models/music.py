from typing import Literal

from pydantic import BaseModel, Field


class Music(BaseModel):
    mood: str = Field(min_length=1, max_length=100)
    genre: str = Field(min_length=1, max_length=100)
    tempo_bpm: int = Field(ge=40, le=240)
    volume: float = Field(ge=0, le=1)
    usage: Literal["background", "transition", "outro"] = "background"
