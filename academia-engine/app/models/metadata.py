from pydantic import BaseModel, Field, model_validator


class Metadata(BaseModel):
    topic: str = Field(min_length=1, max_length=200)
    language: str = Field(pattern=r"^[a-z]{2}(?:-[A-Z]{2})?$")
    target_age_min: int = Field(ge=0, le=18)
    target_age_max: int = Field(ge=0, le=18)
    tags: list[str] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def validate_age_range(self) -> "Metadata":
        if self.target_age_min > self.target_age_max:
            raise ValueError("target_age_min must be less than or equal to target_age_max")
        return self
