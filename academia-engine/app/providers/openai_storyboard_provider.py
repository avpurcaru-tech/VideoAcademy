import os

from openai import APIConnectionError, APIError, APITimeoutError, AuthenticationError, OpenAI, RateLimitError
from pydantic import ValidationError

from app.storyboard.contracts import CreativeStoryboard


DEFAULT_OPENAI_STORYBOARD_MODEL = "gpt-5.6"


class OpenAIStoryboardError(RuntimeError): pass
class OpenAIStoryboardConfigurationError(OpenAIStoryboardError): pass
class OpenAIStoryboardAuthenticationError(OpenAIStoryboardError): pass
class OpenAIStoryboardRateLimitError(OpenAIStoryboardError): pass
class OpenAIStoryboardTimeoutError(OpenAIStoryboardError): pass
class OpenAIStoryboardConnectionError(OpenAIStoryboardError): pass
class OpenAIStoryboardAPIError(OpenAIStoryboardError): pass
class OpenAIStoryboardStructuredOutputError(OpenAIStoryboardError): pass


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
            raise OpenAIStoryboardConfigurationError("OpenAI storyboard client could not be configured.") from error

    def generate_storyboard(self, brief):
        try:
            response = self._client.responses.parse(model=self._model, input=_input(brief), text_format=CreativeStoryboard)
        except ValidationError as error:
            raise OpenAIStoryboardStructuredOutputError("OpenAI storyboard output is malformed.") from error
        except AuthenticationError as error:
            raise OpenAIStoryboardAuthenticationError("OpenAI storyboard authentication failed.") from error
        except RateLimitError as error:
            raise OpenAIStoryboardRateLimitError("OpenAI storyboard rate limit was reached.") from error
        except APITimeoutError as error:
            raise OpenAIStoryboardTimeoutError("OpenAI storyboard request timed out.") from error
        except APIConnectionError as error:
            raise OpenAIStoryboardConnectionError("OpenAI storyboard request could not connect.") from error
        except APIError as error:
            raise OpenAIStoryboardAPIError("OpenAI storyboard API request failed.") from error
        except Exception as error:
            raise OpenAIStoryboardAPIError("OpenAI storyboard request failed safely.") from error
        storyboard = getattr(response, "output_parsed", None)
        if not isinstance(storyboard, CreativeStoryboard):
            raise OpenAIStoryboardStructuredOutputError("OpenAI returned no valid structured storyboard.")
        return storyboard


def _input(brief):
    return [{"role": "system", "content": (
        "Create an original provider-neutral educational creative storyboard. Return only durable creative semantics. "
        "Include original musical style, mood, tempo, vocal direction, and instrumentation in music_direction. "
        "Do not include API payloads, provider names, model settings, URLs, credentials, rendering commands, or implementation details. "
        "Use contiguous section orders starting at one, unique IDs, positive durations, and section durations that exactly total the target.")},
        {"role": "user", "content": (
            f"Storyboard ID: {brief.brief_id}\nTopic: {brief.topic}\nEducational goals: {'; '.join(brief.learning_objectives)}\n"
            f"Language: {brief.language}\nAudience ages: {brief.target_age_min}-{brief.target_age_max}\n"
            f"Target duration seconds: {brief.target_duration_seconds:g}\nSections: {brief.scene_count}\nTone: {brief.tone}\n"
            f"Visual style: {brief.visual_style}\nCharacter hint: {brief.main_character_hint or 'original friendly guide'}\n"
            f"Environment hint: {brief.location_hint or 'safe cheerful learning environment'}") }]
