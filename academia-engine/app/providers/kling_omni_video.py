"""Kling 3.0 Omni request contract for identity-only image references."""
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,HttpUrl


class KlingOmniReferenceImage(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    image_url:HttpUrl


class KlingOmniWatermarkInfo(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    enabled:bool=False


class KlingOmniVideoRequest(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    model_name:Literal["kling-v3-omni"]="kling-v3-omni"
    prompt:str=Field(min_length=1,max_length=3072)
    image_list:tuple[KlingOmniReferenceImage,...]=Field(min_length=1,max_length=4)
    mode:Literal["std"]="std"
    aspect_ratio:Literal["16:9"]="16:9"
    duration:str
    multi_shot:bool=True
    sound:Literal["off"]="off"
    watermark_info:KlingOmniWatermarkInfo=KlingOmniWatermarkInfo()
    callback_url:str|None=None
    external_task_id:str=Field(min_length=1)
    def to_payload(self): return self.model_dump(mode="json",exclude_none=True)


class KlingOmniUiPromptMapper:
    def __init__(self,prompt,reference_urls,settings):
        self.prompt=prompt; self.reference_urls=tuple(reference_urls); self.settings=settings
    def map(self,request,external_task_id,callback_url=None):
        references=" ".join(f"Use <<<image_{index}>>> only as the canonical identity reference for character {character_id}; do not use it as the first frame."
            for index,(character_id,_url) in enumerate(self.reference_urls,1))
        return KlingOmniVideoRequest(prompt=f"{references}\n\n{self.prompt}",
            image_list=tuple(KlingOmniReferenceImage(image_url=url) for _character_id,url in self.reference_urls),
            duration=str(self.settings.duration),multi_shot=self.settings.multi_shot,callback_url=callback_url,
            external_task_id=external_task_id)
