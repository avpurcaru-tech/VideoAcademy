from __future__ import annotations

import json
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SONG_DURATION_TOLERANCE_SECONDS = 1.0


def _text(value: str, label: str) -> str:
    if "\0" in value: raise ValueError(f"{label} must not contain null characters.")
    if not value.strip(): raise ValueError(f"{label} must not be blank.")
    return value


class SongContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    def to_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_json(cls, value: str):
        return cls.model_validate_json(value)


class EducationalSongBrief(SongContract):
    song_id: str = Field(min_length=1, max_length=200)
    topic: str = Field(min_length=1, max_length=500)
    learning_objectives: tuple[str, ...] = Field(min_length=1)
    language: str = Field(min_length=1, max_length=100)
    target_age_min: int = Field(ge=0)
    target_age_max: int = Field(ge=0)
    target_duration_seconds: float = Field(gt=0)
    tone: str = Field(min_length=1, max_length=200)
    repetition_level: str = Field(min_length=1, max_length=100)

    @field_validator("song_id", "topic", "language", "tone", "repetition_level")
    @classmethod
    def validate_text(cls, value: str, info): return _text(value, info.field_name)

    @field_validator("learning_objectives")
    @classmethod
    def validate_objectives(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_text(value, "learning objective") for value in values)

    @model_validator(mode="after")
    def validate_age_range(self):
        if self.target_age_max < self.target_age_min:
            raise ValueError("Target maximum age must be greater than or equal to minimum age.")
        return self


class LyricsSectionKind(str, Enum):
    INTRO = "intro"
    VERSE = "verse"
    CHORUS = "chorus"
    BRIDGE = "bridge"
    OUTRO = "outro"


class LyricsLine(SongContract):
    line_id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1)

    @field_validator("line_id", "text")
    @classmethod
    def validate_text(cls, value: str, info): return _text(value, info.field_name)


class LyricsSection(SongContract):
    section_id: str = Field(min_length=1, max_length=200)
    kind: LyricsSectionKind
    order: int = Field(ge=0)
    lines: tuple[LyricsLine, ...] = Field(min_length=1)

    @field_validator("section_id")
    @classmethod
    def validate_id(cls, value: str): return _text(value, "section_id")


class LyricsPlan(SongContract):
    song_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=500)
    language: str = Field(min_length=1, max_length=100)
    sections: tuple[LyricsSection, ...] = Field(min_length=1)

    @field_validator("song_id", "title", "language")
    @classmethod
    def validate_text(cls, value: str, info): return _text(value, info.field_name)

    @field_validator("sections")
    @classmethod
    def order_sections(cls, sections: tuple[LyricsSection, ...]) -> tuple[LyricsSection, ...]:
        return tuple(sorted(sections,key=lambda section: section.order))

    @model_validator(mode="after")
    def validate_structure(self):
        section_ids=[section.section_id for section in self.sections]
        orders=[section.order for section in self.sections]
        line_ids=[line.line_id for section in self.sections for line in section.lines]
        if len(section_ids) != len(set(section_ids)): raise ValueError("Lyrics section IDs must be unique.")
        if len(orders) != len(set(orders)): raise ValueError("Lyrics section orders must be unique.")
        if len(line_ids) != len(set(line_ids)): raise ValueError("Lyrics line IDs must be unique within the song.")
        kinds={section.kind for section in self.sections}
        if LyricsSectionKind.VERSE not in kinds: raise ValueError("Educational lyrics require at least one verse.")
        if LyricsSectionKind.CHORUS not in kinds: raise ValueError("Educational lyrics require at least one chorus.")
        return self


class MusicPlan(SongContract):
    song_id: str = Field(min_length=1, max_length=200)
    tempo_bpm: float = Field(gt=0)
    musical_style: str = Field(min_length=1, max_length=200)
    mood: str = Field(min_length=1, max_length=200)
    instrumentation: tuple[str, ...] = Field(min_length=1)
    vocal_style: str = Field(min_length=1, max_length=200)
    target_duration_seconds: float = Field(gt=0)

    @field_validator("song_id", "musical_style", "mood", "vocal_style")
    @classmethod
    def validate_text(cls, value: str, info): return _text(value, info.field_name)

    @field_validator("instrumentation")
    @classmethod
    def validate_instrumentation(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_text(value, "instrumentation") for value in values)


class SongProductionPlan(SongContract):
    brief: EducationalSongBrief
    lyrics: LyricsPlan
    music: MusicPlan

    @model_validator(mode="after")
    def validate_consistency(self):
        if not (self.brief.song_id == self.lyrics.song_id == self.music.song_id):
            raise ValueError("Song IDs must match across brief, lyrics, and music plans.")
        if self.brief.language != self.lyrics.language:
            raise ValueError("Song brief and lyrics languages must match.")
        if abs(self.brief.target_duration_seconds-self.music.target_duration_seconds) > SONG_DURATION_TOLERANCE_SECONDS:
            raise ValueError("Music target duration does not match the educational song brief.")
        return self


class ResolvedLyricsPlan(SongContract):
    song_id: str
    title: str
    language: str
    sections: tuple[LyricsSection, ...]
    structural_order: tuple[str, ...]
