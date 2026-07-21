from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StoryboardAudience(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    target_age_min: int = Field(ge=0)
    target_age_max: int = Field(ge=0)

    @model_validator(mode="after")
    def valid_range(self):
        if self.target_age_max < self.target_age_min:
            raise ValueError("Storyboard audience age range is invalid.")
        return self


class StoryboardMusicDirection(BaseModel):
    """Provider-neutral durable musical creative direction."""
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    style: str = Field(min_length=1, max_length=200)
    mood: str = Field(min_length=1, max_length=200)
    tempo_bpm: float = Field(gt=0)
    vocals: str = Field(min_length=1, max_length=200)
    instrumentation: tuple[str, ...] = Field(min_length=1)

    @field_validator("style", "mood", "vocals")
    @classmethod
    def safe_text(cls, value):
        if not value.strip() or "\0" in value: raise ValueError("Storyboard music text must be non-blank and safe.")
        return value

    @field_validator("instrumentation")
    @classmethod
    def safe_instrumentation(cls, values):
        if any(not value.strip() or "\0" in value for value in values):
            raise ValueError("Storyboard instrumentation must be non-blank and safe.")
        return values


class StoryboardSection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    section_id: str = Field(min_length=1, max_length=200, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    order: int = Field(ge=1)
    section_type: str = Field(min_length=1, max_length=100)
    educational_goal: str = Field(min_length=1, max_length=1000)
    learning_focus: str = Field(min_length=1, max_length=1000)
    visual_goal: str = Field(min_length=1, max_length=2000)
    lyrics: str = Field(min_length=1, max_length=10000)
    characters: tuple[str, ...]
    objects: tuple[str, ...]
    environment: str = Field(min_length=1, max_length=2000)
    camera_direction: str = Field(min_length=1, max_length=1000)
    emotion: str = Field(min_length=1, max_length=200)
    estimated_duration_seconds: float = Field(gt=0)

    @field_validator("section_type", "educational_goal", "learning_focus", "visual_goal", "lyrics",
                     "environment", "camera_direction", "emotion")
    @classmethod
    def safe_text(cls, value):
        if not value.strip() or "\0" in value:
            raise ValueError("Storyboard text must be non-blank and safe.")
        return value

    @field_validator("characters", "objects")
    @classmethod
    def safe_lists(cls, values):
        if any(not value.strip() or "\0" in value for value in values):
            raise ValueError("Storyboard names must be non-blank and safe.")
        return values


class CreativeStoryboard(BaseModel):
    """Authoritative provider-neutral durable creative document."""
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    storyboard_id: str = Field(min_length=1, max_length=200, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    title: str = Field(min_length=1, max_length=500)
    language: str = Field(min_length=1, max_length=100)
    audience: StoryboardAudience
    educational_goal: str = Field(min_length=1, max_length=2000)
    music_direction: StoryboardMusicDirection = Field(default_factory=lambda: StoryboardMusicDirection(
        style="original educational song", mood="cheerful", tempo_bpm=110,
        vocals="clear child-friendly vocals", instrumentation=("ukulele", "xylophone", "light percussion")))
    target_duration_seconds: float = Field(gt=0)
    sections: tuple[StoryboardSection, ...] = Field(min_length=1)

    @field_validator("title", "language", "educational_goal")
    @classmethod
    def safe_text(cls, value):
        if not value.strip() or "\0" in value:
            raise ValueError("Storyboard text must be non-blank and safe.")
        return value

    @model_validator(mode="after")
    def coherent_structure(self):
        ids = [section.section_id for section in self.sections]
        orders = [section.order for section in self.sections]
        if len(ids) != len(set(ids)):
            raise ValueError("Storyboard section IDs must be unique.")
        if orders != list(range(1, len(self.sections) + 1)):
            raise ValueError("Storyboard sections must be ordered contiguously from one.")
        estimated = sum(section.estimated_duration_seconds for section in self.sections)
        if abs(estimated - self.target_duration_seconds) > 0.01:
            raise ValueError("Storyboard section durations must match the target duration.")
        return self
