import os

from openai import APIConnectionError, APIError, APITimeoutError, AuthenticationError, OpenAI, RateLimitError

from app.music_timeline.contracts import MusicTimeline


DEFAULT_OPENAI_MUSIC_TIMELINE_MODEL = "gpt-5.6"


class OpenAIMusicTimelineError(RuntimeError): pass
class OpenAIMusicTimelineConfigurationError(OpenAIMusicTimelineError): pass
class OpenAIMusicTimelineStructuredOutputError(OpenAIMusicTimelineError): pass


class OpenAIMusicTimelineGenerator:
    def __init__(self, *, client=None, api_key=None, model=None):
        self._model = model or os.environ.get("OPENAI_MUSIC_TIMELINE_MODEL", DEFAULT_OPENAI_MUSIC_TIMELINE_MODEL)
        if not self._model or not self._model.strip() or "\0" in self._model:
            raise OpenAIMusicTimelineConfigurationError("OpenAI music timeline model is invalid.")
        if client is not None: self._client = client; return
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key or not key.strip(): raise OpenAIMusicTimelineConfigurationError("OpenAI credentials are not configured.")
        try: self._client = OpenAI(api_key=key, max_retries=0)
        except Exception as error: raise OpenAIMusicTimelineConfigurationError("OpenAI client is unavailable.") from error

    def generate_timeline(self, storyboard, lyrics, music_duration_seconds):
        try:
            response = self._client.responses.parse(model=self._model,
                input=_input(storyboard, lyrics, music_duration_seconds), text_format=MusicTimeline)
        except (AuthenticationError, RateLimitError, APITimeoutError, APIConnectionError, APIError) as error:
            raise OpenAIMusicTimelineError("OpenAI music timeline request failed safely.") from error
        except Exception as error: raise OpenAIMusicTimelineError("OpenAI music timeline request failed safely.") from error
        timeline = getattr(response, "output_parsed", None)
        if not isinstance(timeline, MusicTimeline):
            raise OpenAIMusicTimelineStructuredOutputError("OpenAI returned no valid structured music timeline.")
        return timeline


def _input(storyboard, lyrics, duration):
    section_text = "\n".join(f"{section.section_id}: {section.lyrics}" for section in storyboard.sections)
    resolved_text = "\n".join(f"{section.section_id}: " + " | ".join(line.text for line in section.lines)
                              for section in lyrics.sections)
    return [{"role":"system","content":(
        "Align each storyboard section to one contiguous interval in the completed music. Preserve storyboard order, start at zero, "
        "end exactly at the measured duration, create no gaps or overlaps, and report an estimated confidence from zero to one. "
        "Return provider-neutral timing metadata only. Do not modify audio or include URLs, provider payloads, or rendering commands.")},
        {"role":"user","content":(
            f"Timeline ID: {storyboard.storyboard_id}-music\nStoryboard ID: {storyboard.storyboard_id}\n"
            f"Measured music duration seconds: {duration}\nStoryboard sections:\n{section_text}\nResolved lyrics:\n{resolved_text}")}]
