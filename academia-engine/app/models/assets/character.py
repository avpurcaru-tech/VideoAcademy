from pydantic import BaseModel, ConfigDict, Field


class Character(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    display_name: str = Field(min_length=1, max_length=100)
    age: int = Field(ge=0, le=130)
    appearance: str = Field(min_length=1, max_length=1000)
    clothing: str = Field(min_length=1, max_length=1000)
    personality: str = Field(min_length=1, max_length=1000)
    voice: str = Field(min_length=1, max_length=500)
    default_emotions: list[str] = Field(min_length=1, max_length=20)
