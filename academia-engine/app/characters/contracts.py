from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, field_validator


class CanonicalVisualReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    local_path: Path
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_type: str = Field(default="image/png", pattern=r"^image/(png|jpeg|webp)$")


class CanonicalCharacterProfile(BaseModel):
    """Provider-neutral durable identity for one recurring character."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    character_id: str = Field(min_length=1, max_length=200, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=100)
    canonical_description: str = Field(min_length=1, max_length=2000)
    personality_traits: tuple[str, ...] = Field(min_length=1)
    behavior_rules: tuple[str, ...] = Field(min_length=1)
    negative_rules: tuple[str, ...] = Field(min_length=1)
    character_type: str | None = Field(default=None, max_length=100)
    age_description: str | None = Field(default=None, max_length=200)
    voice_description: str | None = Field(default=None, max_length=500)
    version: str | None = Field(default=None, max_length=100)
    visual_reference: CanonicalVisualReference | None = None

    @field_validator("name", "canonical_description", "character_type", "age_description", "voice_description", "version")
    @classmethod
    def safe_text(cls, value):
        if value is not None and (not value.strip() or "\0" in value):
            raise ValueError("Canonical character text must be non-blank and safe.")
        return value

    @field_validator("personality_traits", "behavior_rules", "negative_rules")
    @classmethod
    def safe_rules(cls, values):
        if any(not value.strip() or "\0" in value for value in values):
            raise ValueError("Canonical character entries must be non-blank and safe.")
        return values
