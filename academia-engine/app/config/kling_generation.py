import os
from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict, ValidationError

KLING_PROMPT_MAX_CHARACTERS = 3072
KLING_PROMPT_RECOMMENDED_CHARACTERS = 2500


class KlingGenerationConfigurationError(ValueError):
    """Raised when Kling generation settings are not supported by the documented contract."""


class KlingGenerationSettings(BaseModel):
    """Configured defaults for documented Kling Text-to-Video generation fields."""

    model_config = ConfigDict(extra="forbid")

    resolution: Literal["720p"] = "720p"
    duration: Literal[15] = 15
    audio: Literal["off"] = "off"
    multi_shot: Literal[True] = True

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "KlingGenerationSettings":
        source = environment if environment is not None else os.environ
        values: dict[str, object] = {
            "resolution": source.get("KLING_RESOLUTION", "720p").strip(),
            "duration": cls._parse_duration(source.get("KLING_DURATION", "15")),
            "audio": source.get("KLING_AUDIO", "off"),
            "multi_shot": cls._parse_boolean(source.get("KLING_MULTI_SHOT", "true")),
        }
        try:
            return cls.model_validate(values)
        except ValidationError as error:
            raise KlingGenerationConfigurationError(
                "Kling generation settings contain a value not confirmed by the current official contract."
            ) from error

    @staticmethod
    def _parse_duration(value: str) -> int:
        try:
            return int(value)
        except ValueError as error:
            raise KlingGenerationConfigurationError("KLING_DURATION must be an integer.") from error

    @staticmethod
    def _parse_boolean(value: str) -> bool:
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
        raise KlingGenerationConfigurationError("KLING_MULTI_SHOT must be true or false.")
