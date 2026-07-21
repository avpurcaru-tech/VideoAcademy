from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SeriesCharacter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    character_id: str = Field(min_length=1, max_length=200, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=100)
    character_type: str = Field(min_length=1, max_length=100)
    age_description: str = Field(min_length=1, max_length=200)
    appearance: str = Field(min_length=1, max_length=1000)
    clothing: tuple[str, ...] = ()
    personality: tuple[str, ...] = Field(min_length=1)
    behavior_rules: tuple[str, ...] = Field(min_length=1)
    voice_description: str | None = Field(default=None, max_length=500)
    relative_size: str | None = Field(default=None, max_length=200)
    recurring_accessories: tuple[str, ...] = ()

    @field_validator("name", "character_type", "age_description", "appearance", "voice_description", "relative_size")
    @classmethod
    def safe_text(cls, value):
        if value is not None and (not value.strip() or "\0" in value):
            raise ValueError("Series character text must be non-blank and safe.")
        return value

    @field_validator("clothing", "personality", "behavior_rules", "recurring_accessories")
    @classmethod
    def safe_items(cls, values):
        if any(not value.strip() or "\0" in value for value in values):
            raise ValueError("Series character entries must be non-blank and safe.")
        return values

    @property
    def canonical_description(self) -> str:
        parts = [self.age_description, self.appearance]
        if self.clothing: parts.append("clothing: " + ", ".join(self.clothing))
        if self.recurring_accessories: parts.append("recurring accessories: " + ", ".join(self.recurring_accessories))
        return "; ".join(parts)


class SeriesBible(BaseModel):
    """Provider-neutral durable identity and continuity contract for a series."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    series_id: str = Field(min_length=1, max_length=200, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    title: str = Field(min_length=1, max_length=500)
    language: str = Field(min_length=1, max_length=100)
    visual_style: str = Field(min_length=1, max_length=1000)
    required_character_ids: tuple[str, ...] = ()
    # Read-only compatibility for Bibles registered before canonical profiles.
    characters: tuple[SeriesCharacter, ...] = ()
    continuity_rules: tuple[str, ...] = Field(min_length=1)

    @field_validator("title", "language", "visual_style")
    @classmethod
    def safe_text(cls, value):
        if not value.strip() or "\0" in value:
            raise ValueError("Series Bible text must be non-blank and safe.")
        return value

    @field_validator("continuity_rules")
    @classmethod
    def safe_rules(cls, values):
        if any(not value.strip() or "\0" in value for value in values):
            raise ValueError("Continuity rules must be non-blank and safe.")
        return values

    @model_validator(mode="after")
    def unique_characters(self):
        required = self.required_character_ids or tuple(value.character_id for value in self.characters)
        if not required: raise ValueError("Series Bible requires at least one recurring character ID.")
        if any(not value or not value.replace("_","").replace("-","").isalnum() for value in required):
            raise ValueError("Series character IDs must be path-safe.")
        if len(required)!=len(set(required)): raise ValueError("Required series character IDs must be unique.")
        if list(required)!=sorted(required): raise ValueError("Required series character IDs must use deterministic ordering.")
        ids = [value.character_id for value in self.characters]
        names = [value.name.casefold() for value in self.characters]
        if len(ids) != len(set(ids)): raise ValueError("Series character IDs must be unique.")
        if len(names) != len(set(names)): raise ValueError("Series character names must be unique.")
        if ids != sorted(ids): raise ValueError("Series characters must use deterministic character-ID ordering.")
        return self

    @property
    def resolved_character_ids(self):
        return self.required_character_ids or tuple(value.character_id for value in self.characters)
