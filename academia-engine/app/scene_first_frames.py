"""Durable, provider-neutral contextual first-frame planning and preparation."""
import hashlib
import json
import os
from enum import Enum
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel,ConfigDict,Field,model_validator

from app.visual_references import PublishedVisualReference,SceneVisualReference,VisualReferencePublicationRegistry


class SceneFirstFrameFailure(RuntimeError):
    failure_category="scene_first_frame_generation_failed"
class SceneFirstFramePlanFailed(SceneFirstFrameFailure): failure_category="scene_first_frame_plan_failed"
class SceneFirstFrameGenerationFailed(SceneFirstFrameFailure): failure_category="scene_first_frame_generation_failed"
class SceneFirstFrameCastMismatch(SceneFirstFrameFailure): failure_category="scene_first_frame_cast_mismatch"
class SceneFirstFrameBackgroundMissing(SceneFirstFrameFailure): failure_category="scene_first_frame_background_missing"
class SceneFirstFrameObjectMissing(SceneFirstFrameFailure): failure_category="scene_first_frame_object_missing"
class SceneFirstFrameAspectRatioInvalid(SceneFirstFrameFailure): failure_category="scene_first_frame_aspect_ratio_invalid"
class SceneFirstFramePublicationFailed(SceneFirstFrameFailure): failure_category="scene_first_frame_publication_failed"


class SceneFirstFrameStatus(str,Enum):
    PLANNED="planned"; GENERATED="generated"; PUBLISHED="published"


class SceneFirstFramePlan(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    first_frame_id:str=Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    shot_id:str=Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    source_storyboard_section_id:str=Field(min_length=1)
    recurring_character_ids:tuple[str,...]
    canonical_reference_sha256:tuple[str,...]
    background:str=Field(min_length=1)
    required_objects:tuple[str,...]
    character_positions:str=Field(min_length=1)
    camera_framing:str=Field(min_length=1)
    visual_style:str=Field(min_length=1)
    width:int=Field(gt=0)
    height:int=Field(gt=0)

    @model_validator(mode="after")
    def complete(self):
        if not self.background.strip(): raise SceneFirstFrameBackgroundMissing("Contextual first-frame background is missing.")
        if len(self.recurring_character_ids)!=len(self.canonical_reference_sha256):
            raise SceneFirstFramePlanFailed("Every recurring character requires a canonical identity input.")
        return self

    @property
    def aspect_ratio(self): return self.width/self.height

    def prompt(self):
        objects=", ".join(self.required_objects)
        cast=", ".join(self.recurring_character_ids) or "no recurring characters"
        return (f"Create one complete opaque opening frame in {self.visual_style}. Background: {self.background}. "
            f"Required objects: {objects}. Exact cast: {cast}. Positions and action: {self.character_positions}. "
            f"Camera: {self.camera_framing}. Preserve the supplied canonical character identities exactly. "
            "No text, labels, annotations, character-sheet layout, isolated cutouts, transparency, padding or borders.")


class SceneFirstFrame(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    first_frame_id:str
    shot_id:str
    source_storyboard_section_id:str
    recurring_character_ids:tuple[str,...]
    canonical_reference_sha256:tuple[str,...]
    local_path:Path
    sha256:str=Field(pattern=r"^[a-f0-9]{64}$")
    content_type:str=Field(pattern=r"^image/(png|jpeg|webp)$")
    width:int=Field(gt=0)
    height:int=Field(gt=0)
    publication_url:str|None=None
    generation_status:SceneFirstFrameStatus

    def as_visual_reference(self):
        return SceneVisualReference(reference_id=self.first_frame_id,character_ids=self.recurring_character_ids,
            local_path=self.local_path,sha256=self.sha256,content_type=self.content_type,width=self.width,height=self.height)


class GeneratedSceneFirstFrame(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    local_path:Path; content_type:str; width:int; height:int
    character_ids:tuple[str,...]
    opaque:bool=True; character_sheet:bool=False


class SceneFirstFrameGenerator(Protocol):
    def generate(self,plan:SceneFirstFramePlan,identity_references:tuple)->GeneratedSceneFirstFrame: ...


class SceneFirstFrameStore:
    def __init__(self,root:Path|None=None): self._root=root or Path.cwd()/".runtime"/"scene-first-frames"
    def load(self,first_frame_id):
        path=self._root/f"{first_frame_id}.json"
        if not path.is_file(): return None
        return SceneFirstFrame.model_validate_json(path.read_text(encoding="utf-8"))
    def save(self,value:SceneFirstFrame):
        path=self._root/f"{value.first_frame_id}.json"; path.parent.mkdir(parents=True,exist_ok=True)
        part=path.with_suffix(".json.part")
        try:
            part.write_text(json.dumps(value.model_dump(mode="json"),ensure_ascii=False,separators=(",",":")),encoding="utf-8")
            with part.open("r+b") as stream: os.fsync(stream.fileno())
            os.replace(part,path)
        finally: part.unlink(missing_ok=True)


class SceneFirstFrameWorkflow:
    """Generate once, persist before publication, then publish once by SHA-256."""
    def __init__(self,generator,store=None,publications=None,publisher=None,aspect_ratio_tolerance=.01):
        self.generator=generator; self.store=store or SceneFirstFrameStore()
        self.publications=publications or VisualReferencePublicationRegistry(); self.publisher=publisher
        self.tolerance=aspect_ratio_tolerance

    def prepare(self,plan,identity_references):
        existing=self.store.load(plan.first_frame_id)
        if existing is not None:
            self._validate_existing(existing,plan)
            if existing.publication_url: return existing
            return self._publish(existing)
        try: generated=self.generator.generate(plan,identity_references)
        except SceneFirstFrameFailure: raise
        except Exception as error: raise SceneFirstFrameGenerationFailed("Contextual first-frame generation failed.") from error
        if tuple(generated.character_ids)!=plan.recurring_character_ids:
            raise SceneFirstFrameCastMismatch("Contextual first-frame cast does not match the shot cast.")
        if not generated.opaque or generated.character_sheet:
            raise SceneFirstFrameGenerationFailed("Transparent or character-sheet first frames are not allowed.")
        if abs(generated.width/generated.height-plan.aspect_ratio)>self.tolerance:
            raise SceneFirstFrameAspectRatioInvalid("Contextual first-frame aspect ratio is incompatible with video settings.")
        digest=hashlib.sha256(generated.local_path.read_bytes()).hexdigest()
        value=SceneFirstFrame(first_frame_id=plan.first_frame_id,shot_id=plan.shot_id,
            source_storyboard_section_id=plan.source_storyboard_section_id,
            recurring_character_ids=plan.recurring_character_ids,canonical_reference_sha256=plan.canonical_reference_sha256,
            local_path=generated.local_path,sha256=digest,content_type=generated.content_type,width=generated.width,
            height=generated.height,generation_status=SceneFirstFrameStatus.GENERATED)
        self.store.save(value)
        return self._publish(value)

    def _publish(self,value):
        if self.publisher is None: raise SceneFirstFramePublicationFailed("Contextual first-frame publisher is unavailable.")
        try: publication=self.publications.publish_once(value.as_visual_reference(),self.publisher)
        except Exception as error: raise SceneFirstFramePublicationFailed("Contextual first-frame publication failed.") from error
        published=value.model_copy(update={"publication_url":publication.https_url,"generation_status":SceneFirstFrameStatus.PUBLISHED})
        self.store.save(published); return published

    def _validate_existing(self,value,plan):
        if value.recurring_character_ids!=plan.recurring_character_ids: raise SceneFirstFrameCastMismatch("Persisted contextual first-frame cast differs.")
        if abs(value.width/value.height-plan.aspect_ratio)>self.tolerance: raise SceneFirstFrameAspectRatioInvalid("Persisted contextual first-frame aspect ratio differs.")
        if not value.local_path.is_file() or hashlib.sha256(value.local_path.read_bytes()).hexdigest()!=value.sha256:
            raise SceneFirstFrameGenerationFailed("Persisted contextual first-frame failed integrity validation.")
