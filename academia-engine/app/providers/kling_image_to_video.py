from typing import Literal
from uuid import uuid4

from pydantic import BaseModel,ConfigDict,Field,HttpUrl

from app.models import VideoGenerationRequest
from app.visual_references import VisualReferencePublicationRegistry
from app.visual_references import (LUCA_MAX_SCENE_REFERENCE,LUCA_SCENE_REFERENCE,MAX_SCENE_REFERENCE)
from app.scene_first_frames import SceneFirstFrameAspectRatioInvalid
from .kling_dtos import KlingCreateTaskResponse,KlingMalformedResponseError
from .kling_provider import KlingProvider


class KlingPromptContent(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    type: Literal["prompt"]="prompt"
    text: str=Field(min_length=1)

class KlingFirstFrameContent(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    type: Literal["first_frame"]="first_frame"
    url: HttpUrl

class KlingImageSettings(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    resolution: Literal["4k"]="4k"
    duration: Literal[10]=10
    audio: Literal["off"]="off"
    multi_shot: Literal[False]=False

class KlingImageWatermarkInfo(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    enabled: bool=False

class KlingImageOptions(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    callback_url: HttpUrl|None=None
    external_task_id: str=Field(min_length=1)
    watermark_info: KlingImageWatermarkInfo=KlingImageWatermarkInfo()

class KlingImageToVideoRequest(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    contents: tuple[KlingPromptContent,KlingFirstFrameContent]
    settings: KlingImageSettings
    options: KlingImageOptions
    def to_payload(self): return self.model_dump(mode="json",exclude_none=True)


class KlingImageToVideoMapper:
    def __init__(self,references:VisualReferencePublicationRegistry|None=None):
        self._references=references or VisualReferencePublicationRegistry()

    def map(self,request:VideoGenerationRequest,external_task_id:str,callback_url:str|None=None):
        reference=request.scene_visual_reference
        if reference is None: raise ValueError("Image-to-video requires one prepared contextual scene first frame.")
        canonical_hashes={LUCA_MAX_SCENE_REFERENCE.sha256,LUCA_SCENE_REFERENCE.sha256,MAX_SCENE_REFERENCE.sha256}
        if reference.sha256 in canonical_hashes:
            raise ValueError("A generic canonical identity sheet cannot be submitted as a production first_frame.")
        if tuple(value.id for value in request.video_request.characters)!=reference.character_ids:
            raise ValueError("Composite canonical reference does not represent the complete scene cast.")
        if request.video_request.duration_seconds!=10: raise ValueError("Kling image-to-video supports configured duration 10 only.")
        if abs(reference.width/reference.height-16/9)>.01:
            raise SceneFirstFrameAspectRatioInvalid("Contextual first-frame aspect ratio is incompatible with Kling settings.")
        url=self._references.resolve(reference)
        prompt=self._prompt(request)
        return KlingImageToVideoRequest(contents=(KlingPromptContent(text=prompt),KlingFirstFrameContent(url=url)),
            settings=KlingImageSettings(),options=KlingImageOptions(callback_url=callback_url,
                external_task_id=external_task_id,watermark_info=KlingImageWatermarkInfo(enabled=False)))

    def register_contextual_publication(self,reference,url):
        return self._references.register_existing(reference,url)

    @staticmethod
    def _prompt(request):
        value=request.video_request
        # The planner currently projects one shared shot action onto every cast member.
        # Stable de-duplication preserves order if genuinely distinct actions are introduced.
        actions=" ".join(dict.fromkeys(action.action.strip() for action in value.character_actions if action.action.strip()))
        visual_goal=""
        marker="Visual goal: "
        if marker in value.environment.location_description:
            visual_goal=value.environment.location_description.split(marker,1)[1].split("\nObjects:",1)[0].strip()
        objects=""
        object_marker="\nObjects: "
        if object_marker in value.environment.location_description:
            objects=value.environment.location_description.split(object_marker,1)[1].strip()
        reminders=[]
        ids={item.id for item in value.characters}
        if "luca" in ids: reminders.append("Keep Luca's face, golden-blond curly hair, blue eyes, age and clothing unchanged.")
        if "max" in ids: reminders.append("Keep Max a six-month-old black-and-tan German Shepherd puppy with red collar and puppy proportions. Max never speaks.")
        camera=f"Camera movement: {value.camera.movement}; framing remains {value.camera.shot_type}."
        education=f"Maintain clear educational visibility: {visual_goal}." if visual_goal else ""
        interaction=f"Keep these essential objects visible and preserve their interaction: {objects}." if objects and objects!="none specified" else ""
        return " ".join(filter(None,("Continue from the supplied contextual opening frame without rebuilding the location.",
            f"Motion and interaction: {actions}",education,interaction,camera,*reminders))).strip()


class KlingImageToVideoProvider(KlingProvider):
    endpoint="/image-to-video/kling-3.0"
    provider_key="kling_image_to_video"
    supports_text_prompt=True; supports_first_frame=True; supports_single_image_reference=True
    supports_multiple_character_references=False; supports_element_references=False

    @staticmethod
    def capability_snapshot(cost_per_generated_second=None):
        from app.video_coverage import VideoProviderCapabilities
        return VideoProviderCapabilities(provider_name="kling_image_to_video",supported_clip_durations=(10,),
            selected_clip_duration=10,supports_reference_images=True,supports_multiple_references=False,
            cost_per_generated_second=cost_per_generated_second)

    def __init__(self,client,mapper=None,callback_url=None,first_frame_workflow=None):
        self._client=client; self._mapper=mapper or KlingImageToVideoMapper(); self._callback_url=callback_url
        self._first_frame_workflow=first_frame_workflow

    def submit_generation(self,request):
        if request.scene_visual_reference is None and request.scene_first_frame_plan is not None:
            if self._first_frame_workflow is None:
                raise ValueError("Contextual scene first-frame workflow is not configured.")
            frame=self._first_frame_workflow.prepare(request.scene_first_frame_plan,request.character_reference_images)
            reference=frame.as_visual_reference()
            self._mapper.register_contextual_publication(reference,frame.publication_url)
            request=request.model_copy(update={"scene_visual_reference":reference})
        correlation_id=uuid4().hex; mapped=self._mapper.map(request,correlation_id,self._callback_url)
        payload=self._client.post_json(self.endpoint,mapped.to_payload())
        response=KlingCreateTaskResponse.parse(payload)
        if response.data is None: raise KlingMalformedResponseError("Kling Create Task success response is missing data.")
        return self._to_generation_task(task_data=response.data,provider_request_id=response.request_id,
            provider_code=response.code,provider_message=response.message,internal_request_id=request.request_id,
            tolerate_optional_metadata=True)
