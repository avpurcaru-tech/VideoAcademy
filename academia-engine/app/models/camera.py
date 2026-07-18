from typing import Literal

from pydantic import BaseModel, Field


class Camera(BaseModel):
    shot_type: Literal["wide", "medium", "close_up", "extreme_close_up"]
    angle: Literal["eye_level", "high", "low", "bird_eye"] = "eye_level"
    movement: Literal["static", "pan", "tilt", "zoom", "tracking"] = "static"
    description: str = Field(min_length=1, max_length=500)
