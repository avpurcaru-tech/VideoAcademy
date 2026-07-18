from pydantic import BaseModel, ConfigDict, Field


class CharacterAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    character_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    action: str = Field(min_length=1, max_length=1000)
    emotion: str = Field(min_length=1, max_length=100)
