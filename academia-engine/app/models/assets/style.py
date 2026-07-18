from pydantic import BaseModel, ConfigDict, Field


class Style(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=100)
    visual_style: str = Field(min_length=1, max_length=1000)
    palette: list[str] = Field(min_length=1, max_length=20)
    lighting: str = Field(min_length=1, max_length=500)
    rendering: str = Field(min_length=1, max_length=500)
    environment_defaults: dict[str, str] = Field(default_factory=dict)
