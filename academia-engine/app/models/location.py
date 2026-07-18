from pydantic import BaseModel, Field


class Location(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    description: str = Field(min_length=1, max_length=1000)
    time_of_day: str = Field(min_length=1, max_length=100)
