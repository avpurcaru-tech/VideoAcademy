from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Transition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["cut", "dissolve", "fade", "fade_to_black"]
    duration_seconds: float = Field(default=0, ge=0, le=10)
