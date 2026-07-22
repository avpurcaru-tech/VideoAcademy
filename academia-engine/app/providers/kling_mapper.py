from dataclasses import dataclass
import re

from app.config import (KlingGenerationSettings,KLING_PROMPT_MAX_CHARACTERS,
    KLING_PROMPT_RECOMMENDED_CHARACTERS)
from app.models import VideoGenerationRequest

from .kling_dtos import KlingOptions, KlingSettings, KlingTextToVideoRequest, KlingWatermarkInfo


class KlingUnsupportedConfigurationError(ValueError):
    """Raised when a value is not confirmed by the official request example."""
class KlingPromptTooLongError(ValueError): pass

@dataclass(frozen=True)
class KlingPromptLengthDiagnostic:
    before_characters: int
    after_characters: int
    maximum_characters: int=KLING_PROMPT_MAX_CHARACTERS
    recommended_characters: int=KLING_PROMPT_RECOMMENDED_CHARACTERS
    compaction_applied: bool=False


class KlingTextToVideoMapper:
    def __init__(
        self,
        generation_settings: KlingGenerationSettings,
        aspect_ratio: str = "16:9",
        watermark_enabled: bool = False,
    ) -> None:
        if aspect_ratio != "16:9":
            raise KlingUnsupportedConfigurationError(
                "Only aspect ratio '16:9' is confirmed by the official request example."
            )
        self._generation_settings = generation_settings
        self._aspect_ratio = aspect_ratio
        self._watermark_enabled = watermark_enabled

    def map(
        self,
        request: VideoGenerationRequest,
        external_task_id: str,
        callback_url: str | None = None,
    ) -> KlingTextToVideoRequest:
        video_request = request.video_request
        if video_request.duration_seconds != self._generation_settings.duration:
            raise KlingUnsupportedConfigurationError(
                "Video request duration does not match the configured Kling generation duration."
            )
        prompt,diagnostic=self.prompt_with_diagnostic(request)
        if diagnostic.after_characters>KLING_PROMPT_MAX_CHARACTERS:
            raise KlingPromptTooLongError("Kling prompt exceeds the documented maximum after deterministic compaction.")
        return KlingTextToVideoRequest(
            prompt=prompt,
            settings=KlingSettings(
                resolution=self._generation_settings.resolution,
                aspect_ratio=self._aspect_ratio,
                duration=video_request.duration_seconds,
                audio=self._generation_settings.audio,
                multi_shot=self._generation_settings.multi_shot,
            ),
            options=KlingOptions(
                callback_url=callback_url,
                external_task_id=external_task_id,
                watermark_info=KlingWatermarkInfo(enabled=self._watermark_enabled),
            ),
        )

    @staticmethod
    def _build_prompt(request: VideoGenerationRequest) -> str:
        return KlingTextToVideoMapper.prompt_with_diagnostic(request)[0]

    @staticmethod
    def prompt_with_diagnostic(request: VideoGenerationRequest):
        before=KlingTextToVideoMapper._build_prompt_uncompacted(request)
        after=(before if len(before)<=KLING_PROMPT_RECOMMENDED_CHARACTERS else
            KlingTextToVideoMapper._compact_prompt(request))
        return after,KlingPromptLengthDiagnostic(before_characters=len(before),after_characters=len(after),
            compaction_applied=after!=before)

    @staticmethod
    def validate_prompt(prompt):
        if len(prompt)>KLING_PROMPT_MAX_CHARACTERS: raise KlingPromptTooLongError("Kling prompt exceeds the documented maximum.")
        return prompt

    @staticmethod
    def _build_prompt_uncompacted(request: VideoGenerationRequest) -> str:
        video_request = request.video_request
        environment = video_request.environment
        character_details = "; ".join(
            f"{character.name}, {character.role}, {character.appearance}"
            for character in video_request.characters
        ) or "No characters on screen"
        action_details = "; ".join(
            f"{action.character_id}: {action.action}, emotion: {action.emotion}"
            for action in video_request.character_actions
        ) or "Gentle ambient motion"

        return (
            f"Scene {video_request.scene_number}. "
            f"Environment: {environment.location_name}; {environment.location_description}; "
            f"time of day: {environment.time_of_day}; lighting: {environment.lighting_description}. "
            f"Characters: {character_details}. Actions: {action_details}. "
            f"Camera: {video_request.camera.shot_type} shot, "
            f"{video_request.camera.angle} angle, {video_request.camera.movement} movement. "
            f"Transition: {video_request.transition.type}."
        )

    @staticmethod
    def _compact_prompt(request):
        value=request.video_request; environment=value.environment
        characters="; ".join(KlingTextToVideoMapper._essential_character(item) for item in value.characters) or "No characters on screen"
        unique_actions=[]
        for action in value.character_actions:
            if action.action not in unique_actions: unique_actions.append(action.action)
        actions=" ".join(unique_actions) or "Gentle ambient motion."
        identities=", ".join(f"{item.id} ({item.name})" for item in value.characters)
        description=" ".join(environment.location_description.split())
        if description.startswith(environment.location_name): description=description[len(environment.location_name):].lstrip(" .;:-")
        prompt=(f"Scene {value.scene_number}. Visual goal and action: {actions} Environment and requested visual style: "
            f"{environment.location_name}; {description} Characters by ID and name: {identities}. Essential canonical appearance: "
            f"{characters}. Camera direction: {value.camera.description}; {value.camera.shot_type} shot, {value.camera.angle} angle, "
            f"{value.camera.movement} movement. Transition: {value.transition.type}.")
        return re.sub(r"\s+"," ",prompt).strip()

    @staticmethod
    def _essential_character(character):
        block=" ".join(character.appearance.split()); lowered=character.id.casefold()
        canonical=block.split("Behavior rules:",1)[0].replace(f"Canonical character {character.name}:","").strip()
        sentences=[item.strip()+"." for item in canonical.split(".") if item.strip()]
        if lowered=="luca": essential=" ".join(sentences[:2])
        elif lowered=="max": essential=" ".join(sentences[:1])
        else: essential=" ".join(sentences[:1]) or block[:500]
        never_speaks=" Max never speaks." if lowered=="max" and "never speaks" in block.casefold() else ""
        return f"{character.name} [{character.id}], {character.role}: {essential}{never_speaks}".strip()
