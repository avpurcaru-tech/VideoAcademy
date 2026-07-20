from __future__ import annotations

import os
from enum import Enum

from openai import (APIConnectionError, APIError, APITimeoutError, AuthenticationError,
                    OpenAI, RateLimitError)
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.song.contracts import EducationalSongBrief, LyricsLine, LyricsPlan, LyricsSection


DEFAULT_OPENAI_LYRICS_MODEL = "gpt-5.6"


class OpenAILyricsProviderError(RuntimeError): pass
class OpenAILyricsConfigurationError(OpenAILyricsProviderError): pass
class OpenAILyricsAuthenticationError(OpenAILyricsProviderError): pass
class OpenAILyricsRateLimitError(OpenAILyricsProviderError): pass
class OpenAILyricsTimeoutError(OpenAILyricsProviderError): pass
class OpenAILyricsConnectionError(OpenAILyricsProviderError): pass
class OpenAILyricsAPIError(OpenAILyricsProviderError): pass
class OpenAILyricsStructuredResponseError(OpenAILyricsProviderError): pass
class OpenAILyricsRefusalError(OpenAILyricsProviderError): pass


class _SectionKind(str,Enum):
    INTRO="intro"; VERSE="verse"; CHORUS="chorus"; BRIDGE="bridge"; OUTRO="outro"


class _ResponseModel(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)


class _LyricsLineDTO(_ResponseModel):
    line_id: str = Field(min_length=1,max_length=200)
    text: str = Field(min_length=1)

    @field_validator("line_id","text")
    @classmethod
    def safe_nonblank_text(cls,value: str):
        if not value.strip() or "\0" in value: raise ValueError("Value must be non-blank and contain no null character.")
        return value


class _LyricsSectionDTO(_ResponseModel):
    section_id: str = Field(min_length=1,max_length=200)
    kind: _SectionKind
    order: int = Field(ge=0)
    lines: tuple[_LyricsLineDTO,...] = Field(min_length=1)

    @field_validator("section_id")
    @classmethod
    def safe_nonblank_id(cls,value: str):
        if not value.strip() or "\0" in value: raise ValueError("Section ID must be non-blank and contain no null character.")
        return value


class _LyricsResponseDTO(_ResponseModel):
    title: str = Field(min_length=1,max_length=500)
    sections: tuple[_LyricsSectionDTO,...] = Field(min_length=1)

    @field_validator("title")
    @classmethod
    def safe_nonblank_title(cls,value: str):
        if not value.strip() or "\0" in value: raise ValueError("Title must be non-blank and contain no null character.")
        return value

    @model_validator(mode="after")
    def unique_structure(self):
        section_ids=[section.section_id for section in self.sections]
        orders=[section.order for section in self.sections]
        line_ids=[line.line_id for section in self.sections for line in section.lines]
        if len(section_ids)!=len(set(section_ids)): raise ValueError("Section IDs must be unique.")
        if len(orders)!=len(set(orders)): raise ValueError("Section orders must be unique.")
        if len(line_ids)!=len(set(line_ids)): raise ValueError("Line IDs must be unique.")
        return self


class OpenAILyricsGenerator:
    """OpenAI adapter implementing the provider-neutral LyricsGenerator protocol."""

    def __init__(self, *, client=None, api_key: str | None=None, model: str | None=None) -> None:
        selected_model=model or os.environ.get("OPENAI_LYRICS_MODEL",DEFAULT_OPENAI_LYRICS_MODEL)
        if not selected_model or not selected_model.strip() or "\0" in selected_model:
            raise OpenAILyricsConfigurationError("OpenAI lyrics model configuration is invalid.")
        self._model=selected_model
        if client is not None:
            self._client=client
            return
        credential=api_key or os.environ.get("OPENAI_API_KEY")
        if not credential or not credential.strip():
            raise OpenAILyricsConfigurationError("OpenAI lyrics credentials are not configured.")
        try: self._client=OpenAI(api_key=credential,max_retries=0)
        except Exception as error: raise OpenAILyricsConfigurationError("OpenAI lyrics client could not be configured.") from error

    def generate_lyrics(self, brief: EducationalSongBrief) -> LyricsPlan:
        try:
            response=self._client.responses.parse(model=self._model,input=_build_input(brief),text_format=_LyricsResponseDTO)
        except ValidationError as error: raise OpenAILyricsStructuredResponseError("OpenAI structured lyrics are malformed.") from error
        except AuthenticationError as error: raise OpenAILyricsAuthenticationError("OpenAI authentication failed.") from error
        except RateLimitError as error: raise OpenAILyricsRateLimitError("OpenAI rate limit was reached.") from error
        except APITimeoutError as error: raise OpenAILyricsTimeoutError("OpenAI lyrics request timed out.") from error
        except APIConnectionError as error: raise OpenAILyricsConnectionError("OpenAI lyrics request could not connect.") from error
        except APIError as error: raise OpenAILyricsAPIError("OpenAI lyrics API request failed.") from error
        except Exception as error: raise OpenAILyricsAPIError("OpenAI lyrics request failed safely.") from error
        if _contains_refusal(response): raise OpenAILyricsRefusalError("OpenAI declined the lyrics request.")
        generated=getattr(response,"output_parsed",None)
        if not isinstance(generated,_LyricsResponseDTO):
            raise OpenAILyricsStructuredResponseError("OpenAI returned no valid structured lyrics.")
        try:
            return LyricsPlan(song_id=brief.song_id,title=generated.title,language=brief.language,
                              sections=tuple(LyricsSection(section_id=section.section_id,kind=section.kind.value,
                                  order=section.order,lines=tuple(LyricsLine(line_id=line.line_id,text=line.text)
                                                                  for line in section.lines))
                                             for section in generated.sections))
        except Exception as error:
            raise OpenAILyricsStructuredResponseError("OpenAI structured lyrics violate the semantic contract.") from error


def _build_input(brief: EducationalSongBrief) -> list[dict[str,str]]:
    objectives="\n".join(f"- {objective}" for objective in brief.learning_objectives)
    return [
        {"role":"system","content":(
            "Create newly written, original educational song lyrics for preschool children. "
            "Use simple vocabulary, short singable lines, useful repetition, a clear learning objective, "
            "and age-appropriate content. Include at least one verse and one chorus. "
            "Never imitate or write in the style of any artist or existing song.")},
        {"role":"user","content":(
            f"Topic: {brief.topic}\nLearning objectives:\n{objectives}\n"
            f"Target age: {brief.target_age_min}-{brief.target_age_max}\nLanguage: {brief.language}\n"
            f"Approximate duration seconds: {brief.target_duration_seconds}\nTone: {brief.tone}\n"
            f"Repetition level: {brief.repetition_level}")},
    ]


def _contains_refusal(response) -> bool:
    for output in getattr(response,"output",()) or ():
        if getattr(output,"type",None)!="message": continue
        for item in getattr(output,"content",()) or ():
            if getattr(item,"type",None)=="refusal": return True
    return False
