import os

from openai import OpenAI

from app.models import Episode

from .request import StoryRequest


class OpenAIStoryGenerator:
    def __init__(self, model: str | None = None) -> None:
        self._model = model or os.environ.get("OPENAI_STORY_MODEL", "gpt-5.4-mini")
        self._client = OpenAI()

    def generate(self, request: StoryRequest) -> Episode:
        completion = self._client.beta.chat.completions.parse(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Create safe, age-appropriate educational episodes for children. "
                        "Use the provided characters exactly and return the requested schema."
                    ),
                },
                {
                    "role": "user",
                    "content": self._build_prompt(request),
                },
            ],
            response_format=Episode,
        )
        message = completion.choices[0].message
        if message.parsed is None:
            raise RuntimeError("OpenAI did not return a structured episode")
        return message.parsed

    @staticmethod
    def _build_prompt(request: StoryRequest) -> str:
        return (
            f"Topic: {request.topic}\n"
            f"Language: {request.language}\n"
            f"Target duration in seconds: {request.duration_seconds}\n"
            f"Characters: {request.characters}\n"
            "Create an episode with title, lyrics, metadata, and a scene-by-scene storyboard."
        )
