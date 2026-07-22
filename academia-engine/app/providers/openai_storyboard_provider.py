import os

from openai import APIConnectionError, APIError, APITimeoutError, AuthenticationError, OpenAI, RateLimitError
from pydantic import BaseModel,ConfigDict,Field,ValidationError

from app.storyboard.contracts import CreativeStoryboard,StoryboardAudience,StoryboardMusicDirection,StoryboardSection


DEFAULT_OPENAI_STORYBOARD_MODEL = "gpt-5.6"


class OpenAIStoryboardError(RuntimeError): pass
class OpenAIStoryboardConfigurationError(OpenAIStoryboardError): pass
class OpenAIStoryboardUnavailableError(OpenAIStoryboardError): pass
class OpenAIStoryboardAuthenticationError(OpenAIStoryboardError): pass
class OpenAIStoryboardRateLimitError(OpenAIStoryboardError): pass
class OpenAIStoryboardTimeoutError(OpenAIStoryboardError): pass
class OpenAIStoryboardConnectionError(OpenAIStoryboardError): pass
class OpenAIStoryboardAPIError(OpenAIStoryboardError): pass
class OpenAIStoryboardStructuredOutputError(OpenAIStoryboardError): pass
class OpenAIStoryboardStructuredOutputMissingError(OpenAIStoryboardStructuredOutputError): pass
class OpenAIStoryboardStructuredOutputMalformedError(OpenAIStoryboardStructuredOutputError): pass
class OpenAIStoryboardRefusalError(OpenAIStoryboardError): pass

class OpenAIStoryboardDTO(BaseModel):
    """Provider output: creative semantics and canonical IDs, never canonical identity prose."""
    model_config=ConfigDict(extra="forbid",frozen=True)
    storyboard_id: str
    series_id: str|None=None
    title: str
    language: str
    audience: StoryboardAudience
    educational_goal: str
    music_direction: StoryboardMusicDirection
    target_duration_seconds: float
    required_character_ids: tuple[str,...]=()
    sections: tuple[StoryboardSection,...]=Field(min_length=1)

def _refused(response):
    return any(getattr(content,"type",None)=="refusal" for item in getattr(response,"output",())
        for content in getattr(item,"content",()))

def _decorate(error,source,model):
    error.model=model
    response=getattr(source,"response",None)
    error.http_status=getattr(source,"status_code",None) or getattr(response,"status_code",None)
    error.request_id=getattr(source,"request_id",None)
    headers=getattr(response,"headers",{}) or {}
    error.retry_after=headers.get("retry-after") if hasattr(headers,"get") else None
    return error


class OpenAIStoryboardGenerator:
    """OpenAI adapter for the provider-neutral StoryboardGenerator protocol."""
    def __init__(self, *, client=None, api_key=None, model=None):
        self._model = model or os.environ.get("OPENAI_STORYBOARD_MODEL", DEFAULT_OPENAI_STORYBOARD_MODEL)
        if not self._model or not self._model.strip() or "\0" in self._model:
            raise OpenAIStoryboardConfigurationError("OpenAI storyboard model is invalid.")
        if client is not None:
            self._client = client
            return
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key or not key.strip():
            raise OpenAIStoryboardConfigurationError("OpenAI storyboard credentials are not configured.")
        try:
            self._client = OpenAI(api_key=key, max_retries=0)
        except Exception as error:
            raise OpenAIStoryboardUnavailableError("OpenAI storyboard client is unavailable.") from error

    def generate_storyboard(self, brief, series_bible=None, character_profiles=()):
        try:
            response = self._client.responses.parse(model=self._model, input=_input(brief, series_bible, character_profiles), text_format=OpenAIStoryboardDTO)
        except ValidationError as error:
            raise OpenAIStoryboardStructuredOutputMalformedError("OpenAI storyboard output is malformed.") from error
        except AuthenticationError as error:
            raise _decorate(OpenAIStoryboardAuthenticationError("OpenAI storyboard authentication failed."),error,self._model) from error
        except RateLimitError as error:
            raise _decorate(OpenAIStoryboardRateLimitError("OpenAI storyboard rate limit was reached."),error,self._model) from error
        except APITimeoutError as error:
            raise _decorate(OpenAIStoryboardTimeoutError("OpenAI storyboard request timed out."),error,self._model) from error
        except APIConnectionError as error:
            raise _decorate(OpenAIStoryboardConnectionError("OpenAI storyboard request could not connect."),error,self._model) from error
        except APIError as error:
            raise _decorate(OpenAIStoryboardAPIError("OpenAI storyboard API request failed."),error,self._model) from error
        except Exception as error:
            raise _decorate(OpenAIStoryboardAPIError("OpenAI storyboard request failed safely."),error,self._model) from error
        if _refused(response):
            raise _decorate(OpenAIStoryboardRefusalError("OpenAI declined the storyboard request."),response,self._model)
        storyboard = getattr(response, "output_parsed", None)
        if isinstance(storyboard,CreativeStoryboard):
            storyboard=OpenAIStoryboardDTO.model_validate(storyboard.model_dump(mode="python"))
        if not isinstance(storyboard, OpenAIStoryboardDTO):
            raise _decorate(OpenAIStoryboardStructuredOutputMissingError("OpenAI returned no valid structured storyboard."),response,self._model)
        try: return CreativeStoryboard.model_validate(storyboard.model_dump(mode="python"))
        except ValidationError as error: raise OpenAIStoryboardStructuredOutputMalformedError("OpenAI storyboard mapping failed.") from error


def _input(brief, series_bible=None, character_profiles=()):
    continuity = ""
    if series_bible is not None:
        characters = []
        for value in character_profiles:
            characters.append(f"{value.character_id} | exact name: {value.name} | canonical description: {value.canonical_description} | "
                f"behavior: {'; '.join(value.behavior_rules)} | negative rules: {'; '.join(value.negative_rules)}")
        continuity = (f"\nSeries ID: {series_bible.series_id}\nCanonical visual style: {series_bible.visual_style}\n"
            f"Canonical recurring characters:\n" + "\n".join(characters) + "\nContinuity rules: " +
            "; ".join(series_bible.continuity_rules))
    return [{"role": "system", "content": (
        "Create an original provider-neutral educational creative storyboard. Return only durable creative semantics. "
        "Include original musical style, mood, tempo, vocal direction, and instrumentation in music_direction. "
        "Do not include API payloads, provider names, model settings, URLs, credentials, rendering commands, or implementation details. "
        "Use contiguous section orders starting at one, unique IDs, positive durations, and section durations that exactly total the target. "
        "When series context is supplied, return required characters only as stable IDs in required_character_ids and each section.characters. "
        "Reference canonical characters only by their IDs and names. Do not restate or rewrite appearance. Do not define clothing, age, breed, "
        "eye color, hair color, accessories, canonical descriptions, personality profiles, or negative rules. Max must not speak. "
        "Never rename characters or violate behavior rules. Locations, backgrounds, props, actions, gestures, emotions, and weather may vary freely. "
        "Do not imitate copyrighted characters or franchises.")},
        {"role": "user", "content": (
            f"Storyboard ID: {brief.brief_id}\nTopic: {brief.topic}\nEducational goals: {'; '.join(brief.learning_objectives)}\n"
            f"Language: {brief.language}\nAudience ages: {brief.target_age_min}-{brief.target_age_max}\n"
            f"Target duration seconds: {brief.target_duration_seconds:g}\nSections: {brief.scene_count}\nTone: {brief.tone}\n"
            f"Visual style: {brief.visual_style}\nCharacter hint: {brief.main_character_hint or 'original friendly guide'}\n"
            f"Environment hint: {brief.location_hint or 'safe cheerful learning environment'}{continuity}") }]
