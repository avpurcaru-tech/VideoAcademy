from __future__ import annotations

import os
from typing import Any

from src.models import Episode


class OpenAIStoryModel:
    """OpenAI adapter; the engine itself depends only on the StoryModel protocol."""

    def __init__(self, model_name: str | None = None) -> None:
        self._model_name = model_name or os.environ.get("STORY_MODEL", "gpt-5-mini")

    def generate(self, topic: str) -> Episode:
        from openai import OpenAI

        client = OpenAI()
        completion = client.chat.completions.create(
            model=self._model_name,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You create safe, playful educational episodes for children. "
                        "Return only JSON matching the provided schema."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Create an educational episode about: {topic}",
                },
            ],
            response_format=self._response_format(),
        )
        content = completion.choices[0].message.content
        if content is None:
            raise ValueError("OpenAI returned an empty story response")
        return Episode.model_validate_json(content)

    @staticmethod
    def _response_format() -> dict[str, Any]:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "episode",
                "strict": True,
                "schema": Episode.model_json_schema(),
            },
        }
