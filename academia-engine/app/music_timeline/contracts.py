from pydantic import BaseModel, ConfigDict, Field, model_validator


TIMELINE_TOLERANCE_SECONDS = 0.01


class MusicTimelineSegment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    storyboard_section_id: str = Field(min_length=1, max_length=200)
    estimated_confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def positive_interval(self):
        if self.end_seconds <= self.start_seconds:
            raise ValueError("Timeline segment end must follow its start.")
        return self


class MusicTimeline(BaseModel):
    """Durable storyboard-to-music alignment metadata."""
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    timeline_id: str = Field(min_length=1, max_length=200, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    storyboard_id: str = Field(min_length=1, max_length=200, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    music_duration_seconds: float = Field(gt=0)
    segments: tuple[MusicTimelineSegment, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def contiguous_complete_timeline(self):
        if abs(self.segments[0].start_seconds) > TIMELINE_TOLERANCE_SECONDS:
            raise ValueError("Music timeline must start at zero.")
        for previous, current in zip(self.segments, self.segments[1:]):
            if abs(previous.end_seconds - current.start_seconds) > TIMELINE_TOLERANCE_SECONDS:
                raise ValueError("Music timeline segments must be contiguous and ordered.")
        if abs(self.segments[-1].end_seconds - self.music_duration_seconds) > TIMELINE_TOLERANCE_SECONDS:
            raise ValueError("Music timeline must end at the measured music duration.")
        ids = [segment.storyboard_section_id for segment in self.segments]
        if len(ids) != len(set(ids)):
            raise ValueError("Music timeline storyboard section IDs must be unique.")
        return self
