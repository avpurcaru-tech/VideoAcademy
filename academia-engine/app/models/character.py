from pydantic import BaseModel, Field


class Character(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=100)
    role: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=1000)
    appearance: str = Field(min_length=1, max_length=1000)
