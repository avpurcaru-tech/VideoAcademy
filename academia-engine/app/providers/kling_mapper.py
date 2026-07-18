from app.config import KlingGenerationSettings
from app.models import VideoGenerationRequest

from .kling_dtos import KlingOptions, KlingSettings, KlingTextToVideoRequest, KlingWatermarkInfo


class KlingUnsupportedConfigurationError(ValueError):
    """Raised when a value is not confirmed by the official request example."""


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
        return KlingTextToVideoRequest(
            prompt=self._build_prompt(request),
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
