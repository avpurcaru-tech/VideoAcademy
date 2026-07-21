import hashlib

from app.models import (Camera, CharacterAction, Transition, VideoCharacter,
    VideoEnvironment, VideoGenerationRequest, VideoRequest)
from app.storyboard.contracts import CreativeStoryboard

from .duration_policy import SceneDurationPolicy


class StoryboardVideoPlanningError(RuntimeError): pass


class StoryboardVideoPlanner:
    """Deterministic storyboard-only projection into provider-neutral video requests."""
    def __init__(self, duration_policy: SceneDurationPolicy | None = None):
        self._duration_policy = duration_policy or SceneDurationPolicy(15)

    def build(self, storyboard: CreativeStoryboard, production_id: str) -> tuple[VideoGenerationRequest, ...]:
        try:
            storyboard = CreativeStoryboard.model_validate(storyboard)
            requests = []
            for scene_number, section in enumerate(storyboard.sections, start=1):
                characters = [VideoCharacter(id=self._character_id(storyboard.storyboard_id, name),
                    name=name, role="storyboard character", appearance=section.visual_goal[:1000])
                    for name in section.characters]
                objects = ", ".join(section.objects) if section.objects else "none specified"
                semantic_description = (
                    f"{section.environment}\nVisual goal: {section.visual_goal}\nObjects: {objects}"
                )
                action = f"{section.visual_goal} Objects in the scene: {objects}."
                video_request = VideoRequest(scene_number=scene_number,
                    duration_seconds=self._duration_policy.execution_duration_seconds,
                    environment=VideoEnvironment(location_name=section.environment[:150],
                        location_description=semantic_description[:1000], time_of_day="unspecified",
                        lighting_description=section.emotion[:500], lighting_intensity="medium"),
                    characters=characters,
                    character_actions=[CharacterAction(character_id=character.id, action=action[:1000],
                        emotion=section.emotion[:100]) for character in characters],
                    camera=self._camera(section.camera_direction), transition=Transition(type="cut"))
                # Applying the shared policy here keeps execution duration behavior identical to the legacy planner.
                video_request = self._duration_policy.apply_execution_duration(video_request)
                requests.append(VideoGenerationRequest(
                    request_id=f"{production_id}-scene-{scene_number:04d}", video_request=video_request))
            return tuple(requests)
        except Exception as error:
            if isinstance(error, StoryboardVideoPlanningError):
                raise
            raise StoryboardVideoPlanningError("Storyboard could not be projected to video requests.") from error

    @staticmethod
    def _character_id(storyboard_id: str, name: str) -> str:
        digest = hashlib.sha256(f"{storyboard_id}\0{name}".encode("utf-8")).hexdigest()[:12]
        return f"{storyboard_id}-character-{digest}"

    @staticmethod
    def _camera(direction: str) -> Camera:
        lowered = direction.lower()
        shot = "close_up" if "close" in lowered else "medium" if "medium" in lowered else "wide"
        angle = "bird_eye" if "bird" in lowered else "high" if "high" in lowered else "low" if "low" in lowered else "eye_level"
        movement = next((value for value in ("pan", "tilt", "zoom", "tracking", "static") if value in lowered), "static")
        return Camera(shot_type=shot, angle=angle, movement=movement, description=direction[:500])
