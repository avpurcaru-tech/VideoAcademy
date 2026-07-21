from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator


MIN_SCENE_COUNT=2
MAX_SCENE_COUNT=12


class EducationalCreativeBrief(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True,allow_inf_nan=False)
    brief_id: str=Field(min_length=1,max_length=200,pattern=r"^[a-z0-9][a-z0-9_-]*$")
    topic: str=Field(min_length=1,max_length=500)
    learning_objectives: tuple[str,...]=Field(min_length=1)
    language: str=Field(min_length=1,max_length=100)
    target_age_min: int=Field(ge=0)
    target_age_max: int=Field(ge=0)
    target_duration_seconds: float=Field(gt=0)
    tone: str=Field(min_length=1,max_length=200)
    visual_style: str=Field(min_length=1,max_length=500)
    main_character_hint: str|None=Field(default=None,max_length=500)
    location_hint: str|None=Field(default=None,max_length=500)
    scene_count: int=Field(ge=MIN_SCENE_COUNT,le=MAX_SCENE_COUNT)
    song_required: bool
    series_id: str|None=Field(default=None,max_length=200,pattern=r"^[a-z0-9][a-z0-9_-]*$")

    @field_validator("topic","language","tone","visual_style","main_character_hint","location_hint")
    @classmethod
    def safe_text(cls,value):
        if value is not None and (not value.strip() or "\0" in value): raise ValueError("Creative brief text must be non-blank and safe.")
        return value
    @field_validator("learning_objectives")
    @classmethod
    def safe_objectives(cls,values):
        if any(not value.strip() or "\0" in value for value in values): raise ValueError("Learning objectives must be non-blank and safe.")
        return values
    @model_validator(mode="after")
    def age_range(self):
        if self.target_age_max<self.target_age_min: raise ValueError("Target age range is invalid.")
        return self
