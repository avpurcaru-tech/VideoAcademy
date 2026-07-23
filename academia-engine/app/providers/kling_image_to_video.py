from typing import Literal
from uuid import uuid4

from pydantic import BaseModel,ConfigDict,Field,HttpUrl

from app.models import VideoGenerationRequest
from app.visual_references import VisualReferencePublicationRegistry
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
        if reference is None: raise ValueError("Image-to-video requires one canonical scene reference.")
        if tuple(value.id for value in request.video_request.characters)!=reference.character_ids:
            raise ValueError("Composite canonical reference does not represent the complete scene cast.")
        if request.video_request.duration_seconds!=10: raise ValueError("Kling image-to-video supports configured duration 10 only.")
        url=self._references.resolve(reference)
        prompt=self._prompt(request)
        return KlingImageToVideoRequest(contents=(KlingPromptContent(text=prompt),KlingFirstFrameContent(url=url)),
            settings=KlingImageSettings(),options=KlingImageOptions(callback_url=callback_url,
                external_task_id=external_task_id,watermark_info=KlingImageWatermarkInfo(enabled=False)))

    @staticmethod
    def _prompt(request):
        value=request.video_request
        actions=" ".join(action.action for action in value.character_actions)
        reminders=[]
        ids={item.id for item in value.characters}
        if "luca" in ids: reminders.append("Preserve Luca's exact face, golden-blond curly hair, age and clothing from the first frame.")
        if "max" in ids: reminders.append("Keep Max a six-month-old German Shepherd puppy with red collar. Max never speaks.")
        return " ".join(("Preserve exact character identity from the first frame.",actions,
            value.camera.description,*reminders)).strip()


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

    def __init__(self,client,mapper=None,callback_url=None):
        self._client=client; self._mapper=mapper or KlingImageToVideoMapper(); self._callback_url=callback_url

    def submit_generation(self,request):
        correlation_id=uuid4().hex; mapped=self._mapper.map(request,correlation_id,self._callback_url)
        payload=self._client.post_json(self.endpoint,mapped.to_payload())
        response=KlingCreateTaskResponse.parse(payload)
        if response.data is None: raise KlingMalformedResponseError("Kling Create Task success response is missing data.")
        return self._to_generation_task(task_data=response.data,provider_request_id=response.request_id,
            provider_code=response.code,provider_message=response.message,internal_request_id=request.request_id,
            tolerate_optional_metadata=True)
