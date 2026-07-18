from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Lighting(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=500)
    intensity: Literal["low", "medium", "high"] = "medium"
