import os
from typing import Literal

from openai import (APIConnectionError,APIError,APITimeoutError,AuthenticationError,OpenAI,RateLimitError)
from pydantic import BaseModel,ConfigDict,Field,ValidationError

from app.creative import EducationalCreativeBrief
from app.models import Camera,Character,Episode,Location,Metadata,Scene


DEFAULT_OPENAI_EPISODE_MODEL="gpt-5.6"


class OpenAIEpisodeProviderError(RuntimeError): pass
class OpenAIEpisodeConfigurationError(OpenAIEpisodeProviderError): pass
class OpenAIEpisodeAuthenticationError(OpenAIEpisodeProviderError): pass
class OpenAIEpisodeRateLimitError(OpenAIEpisodeProviderError): pass
class OpenAIEpisodeTimeoutError(OpenAIEpisodeProviderError): pass
class OpenAIEpisodeNetworkError(OpenAIEpisodeProviderError): pass
class OpenAIEpisodeAPIError(OpenAIEpisodeProviderError): pass
class OpenAIEpisodeStructuredOutputError(OpenAIEpisodeProviderError): pass


class _DTO(BaseModel): model_config=ConfigDict(extra="forbid",frozen=True)
class _CharacterDTO(_DTO):
    name: str=Field(min_length=1,max_length=100); role: str=Field(min_length=1,max_length=100)
    description: str=Field(min_length=1,max_length=1000); appearance: str=Field(min_length=1,max_length=1000)
class _LocationDTO(_DTO):
    name: str=Field(min_length=1,max_length=150); description: str=Field(min_length=1,max_length=1000)
    time_of_day: str=Field(min_length=1,max_length=100)
class _CameraDTO(_DTO):
    shot_type: Literal["wide","medium","close_up","extreme_close_up"]
    angle: Literal["eye_level","high","low","bird_eye"]
    movement: Literal["static","pan","tilt","zoom","tracking"]
    description: str=Field(min_length=1,max_length=500)
class _SceneDTO(_DTO):
    narration: str=Field(min_length=1,max_length=2000); visual_description: str=Field(min_length=1,max_length=2000)
    duration_seconds: int=Field(ge=1,le=300); location: _LocationDTO; camera: _CameraDTO
class OpenAIEpisodeResponseDTO(_DTO):
    title: str=Field(min_length=1,max_length=200); lyrics: str=Field(min_length=1,max_length=10000)
    main_character: _CharacterDTO; scenes: tuple[_SceneDTO,...]=Field(min_length=2,max_length=12)


class OpenAIEpisodeGenerator:
    def __init__(self,*,client=None,api_key=None,model=None):
        self._model=model or os.getenv("OPENAI_EPISODE_MODEL",DEFAULT_OPENAI_EPISODE_MODEL)
        if not self._model or not self._model.strip(): raise OpenAIEpisodeConfigurationError("OpenAI Episode model is missing.")
        if client is not None: self._client=client; return
        key=api_key or os.getenv("OPENAI_API_KEY")
        if not key or not key.strip(): raise OpenAIEpisodeConfigurationError("OpenAI Episode provider configuration is missing.")
        try: self._client=OpenAI(api_key=key,max_retries=0)
        except Exception as error: raise OpenAIEpisodeConfigurationError("OpenAI Episode provider is unavailable.") from error

    def generate_episode(self,brief):
        try: response=self._client.responses.parse(model=self._model,input=_input(brief),text_format=OpenAIEpisodeResponseDTO)
        except ValidationError as error: raise OpenAIEpisodeStructuredOutputError("OpenAI Episode structured output is malformed.") from error
        except AuthenticationError as error: raise OpenAIEpisodeAuthenticationError("OpenAI Episode authentication failed.") from error
        except RateLimitError as error: raise OpenAIEpisodeRateLimitError("OpenAI Episode rate limit was reached.") from error
        except APITimeoutError as error: raise OpenAIEpisodeTimeoutError("OpenAI Episode request timed out.") from error
        except APIConnectionError as error: raise OpenAIEpisodeNetworkError("OpenAI Episode request could not connect.") from error
        except APIError as error: raise OpenAIEpisodeAPIError("OpenAI Episode API request failed.") from error
        except Exception as error: raise OpenAIEpisodeAPIError("OpenAI Episode request failed safely.") from error
        dto=getattr(response,"output_parsed",None)
        if not isinstance(dto,OpenAIEpisodeResponseDTO): raise OpenAIEpisodeStructuredOutputError("OpenAI returned no valid structured Episode.")
        character_id=f"{brief.brief_id}-guide"; character=Character(id=character_id,**dto.main_character.model_dump())
        scenes=tuple(Scene(number=index,narration=value.narration,visual_description=value.visual_description,
            duration_seconds=value.duration_seconds,character_ids=[character_id],location=Location(**value.location.model_dump()),
            camera=Camera(**value.camera.model_dump())) for index,value in enumerate(dto.scenes,start=1))
        return Episode(id=brief.brief_id,title=dto.title,lyrics=dto.lyrics,
            metadata=Metadata(topic=brief.topic,language=brief.language,target_age_min=brief.target_age_min,
                target_age_max=brief.target_age_max,tags=["preschool","educational","original"]),characters=[character],scenes=scenes)


def _input(brief):
    system=("Create original, preschool-safe educational Episode semantics. Use simple visually clear scenes, one learning "
        "objective per scene where practical, one consistent recurring original character, and continuity. Exclude frightening, "
        "violent, sexual, political, unsafe, copyrighted-character, franchise, known-song, and living-artist imitation. "
        "Do not include provider-specific video instructions, payloads, URLs, credentials, or FFmpeg syntax.")
    user=(f"Topic: {brief.topic}\nLearning objectives: {'; '.join(brief.learning_objectives)}\nLanguage: {brief.language}\n"
        f"Target ages: {brief.target_age_min}-{brief.target_age_max}\nTarget duration seconds: {brief.target_duration_seconds:g}\n"
        f"Tone: {brief.tone}\nVisual style: {brief.visual_style}\nScene count: {brief.scene_count}\n"
        f"Character hint: {brief.main_character_hint or 'friendly original guide'}\nLocation hint: {brief.location_hint or 'safe cheerful setting'}\n"
        f"Song required: {'yes' if brief.song_required else 'no'}")
    return [{"role":"system","content":system},{"role":"user","content":user}]
