import hashlib

from app.models import (Camera, CharacterAction, CharacterReferenceImage, Transition, VideoCharacter,
    VideoEnvironment, VideoGenerationRequest, VideoRequest)
from app.storyboard.contracts import CreativeStoryboard

from .duration_policy import SceneDurationPolicy
from app.visual_references import LUCA_MAX_SCENE_REFERENCE,LUCA_SCENE_REFERENCE,MAX_SCENE_REFERENCE
from app.scene_first_frames import SceneFirstFramePlan


class StoryboardVideoPlanningError(RuntimeError): pass
class RecurringCharacterReferenceMissingError(StoryboardVideoPlanningError): pass
class RecurringCharacterReferenceInvalidError(StoryboardVideoPlanningError): pass


class StoryboardVideoPlanner:
    """Deterministic storyboard-only projection into provider-neutral video requests."""
    def __init__(self, duration_policy: SceneDurationPolicy | None = None,character_registry=None,series_registry=None):
        self._duration_policy = duration_policy or SceneDurationPolicy(15)
        self._character_registry=character_registry; self._series_registry=series_registry

    def build(self, storyboard: CreativeStoryboard, production_id: str,coverage_plan=None) -> tuple[VideoGenerationRequest, ...]:
        try:
            storyboard = CreativeStoryboard.model_validate(storyboard)
            canonical = {value.character_id: value for value in storyboard.canonical_characters}
            profiles={}
            if storyboard.series_id:
                from app.characters import CharacterRegistry
                from app.series import SeriesRegistry
                bible=(self._series_registry or SeriesRegistry()).load(storyboard.series_id)
                profiles={value.character_id:value for value in (self._character_registry or CharacterRegistry()).require_many(bible.resolved_character_ids)}
            requests = []
            sections={value.section_id:value for value in storyboard.sections}
            planning=((sections[shot.source_storyboard_section_id],shot) for shot in coverage_plan.unique_shots) if coverage_plan else (
                (section,None) for section in storyboard.sections)
            for scene_number, (section,coverage_shot) in enumerate(planning, start=1):
                characters = [self._video_character(storyboard, reference, canonical,profiles) for reference in section.characters]
                references=[]
                for character_id in section.characters:
                    if character_id not in profiles: continue
                    reference=profiles[character_id].visual_reference
                    if reference is None or not reference.local_path.is_file():
                        raise RecurringCharacterReferenceMissingError(
                            f"Recurring character {character_id} has no available canonical visual reference.")
                    if hashlib.sha256(reference.local_path.read_bytes()).hexdigest()!=reference.sha256:
                        raise RecurringCharacterReferenceInvalidError(
                            f"Recurring character {character_id} canonical visual reference failed integrity validation.")
                    references.append(CharacterReferenceImage(character_id=character_id,local_path=reference.local_path,
                        sha256=reference.sha256,content_type=reference.content_type))
                objects = ", ".join(section.objects) if section.objects else "none specified"
                semantic_description = (
                    f"{section.environment}\nVisual goal: {section.visual_goal}\nObjects: {objects}"
                )
                specific=coverage_shot.action_variation if coverage_shot else " ".join((*section.actions,*section.gestures))
                # Motion is stored separately from the educational goal and object inventory.
                action = specific or "Hold the opening pose and continue with gentle preschool motion."
                video_request = VideoRequest(scene_number=scene_number,
                    duration_seconds=self._duration_policy.execution_duration_seconds,
                    environment=VideoEnvironment(location_name=section.environment[:150],
                        location_description=semantic_description[:1000], time_of_day="unspecified",
                        lighting_description=section.emotion[:500], lighting_intensity="medium"),
                    characters=characters,
                    character_actions=[CharacterAction(character_id=character.id, action=action[:1000],
                        emotion=section.emotion[:100]) for character in characters],
                    camera=self._camera(coverage_shot.camera_variation if coverage_shot else section.camera_direction), transition=Transition(type="cut"))
                # Applying the shared policy here keeps execution duration behavior identical to the legacy planner.
                video_request = self._duration_policy.apply_execution_duration(video_request)
                if coverage_shot: video_request=video_request.model_copy(update={"duration_seconds":int(coverage_shot.duration_seconds)})
                identity_reference=self._scene_reference(section.characters)
                shot_id=coverage_shot.shot_id if coverage_shot else f"shot-{scene_number:04d}"
                first_frame_plan=None
                if identity_reference is not None:
                    first_frame_plan=SceneFirstFramePlan(first_frame_id=f"{production_id}-{shot_id}-first-frame",
                        shot_id=shot_id,source_storyboard_section_id=section.section_id,
                        recurring_character_ids=tuple(section.characters),
                        canonical_reference_sha256=tuple(value.sha256 for value in references),
                        background=section.environment,required_objects=tuple(section.objects),
                        character_positions=specific or "Characters hold the opening pose specified by the storyboard.",
                        camera_framing=coverage_shot.camera_variation if coverage_shot else section.camera_direction,
                        visual_style="high-quality stylized 3D preschool animation with soft rounded forms and warm cinematic lighting",
                        width=1920,height=1080)
                requests.append(VideoGenerationRequest(
                    request_id=f"{production_id}-scene-{scene_number:04d}", video_request=video_request,
                    character_reference_images=tuple(references),identity_visual_reference=identity_reference,
                    scene_first_frame_plan=first_frame_plan,scene_visual_reference=None,
                    planned_shot_id=coverage_shot.shot_id if coverage_shot else None))
            return tuple(requests)
        except Exception as error:
            if isinstance(error, StoryboardVideoPlanningError):
                raise
            raise StoryboardVideoPlanningError("Storyboard could not be projected to video requests.") from error

    @staticmethod
    def _scene_reference(character_ids):
        values=tuple(character_ids)
        recurring=set(values)&{"luca","max"}
        if recurring=={"luca","max"}: return LUCA_MAX_SCENE_REFERENCE
        if recurring=={"luca"}: return LUCA_SCENE_REFERENCE
        if recurring=={"max"}: return MAX_SCENE_REFERENCE
        return None

    @staticmethod
    def _video_character(storyboard, reference, canonical,profiles):
        if reference in profiles:
            value=profiles[reference]
            block=(f"Canonical character {value.name}:\n{value.canonical_description}\nBehavior rules: "
                f"{' '.join(value.behavior_rules)}\nContinuity constraints: {' '.join(value.negative_rules)}")
            return VideoCharacter(id=value.character_id,name=value.name,role=value.character_type or "recurring character",
                appearance=block[:1000])
        if reference in canonical:
            value = canonical[reference]
            description = "; ".join(filter(None, (value.age_description, value.appearance,
                "clothing: " + ", ".join(value.clothing) if value.clothing else "",
                "recurring accessories: " + ", ".join(value.recurring_accessories) if value.recurring_accessories else "")))
            return VideoCharacter(id=value.character_id, name=value.name, role=value.character_type,
                appearance=description[:1000])
        return VideoCharacter(id=StoryboardVideoPlanner._character_id(storyboard.storyboard_id, reference),
            name=reference, role="storyboard character", appearance="Original character as established by the storyboard.")

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
