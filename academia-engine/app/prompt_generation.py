"""Deterministic provider-specific prompt projection from VisualPlan (Sprint 17.3)."""
import json,os,re
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel,ConfigDict,Field

from app.scene_planning import semantic_sha256
from app.visual_planning import VisualPlan,VisualPlanStatus,VisualScene

PROMPT_BUNDLE_SCHEMA_VERSION="1.0"
PROMPT_BUILDER_VERSION="17.3.0"

class PromptGenerationError(RuntimeError): pass
class PromptPersistenceError(PromptGenerationError): pass
class PromptProvider(str,Enum):
    GENERIC_IMAGE="generic_image"; GENERIC_VIDEO="generic_video"; KLING="kling"
class PromptStatus(str,Enum):
    VALID="valid"; VALID_WITH_WARNINGS="valid_with_warnings"; REVIEW_REQUIRED="review_required"; INVALID="invalid"

class PromptCapabilities(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    supports_negative_prompt:bool=True; supports_duration:bool=False; supports_aspect_ratio:bool=True
    supports_seed:bool=False; supports_reference_images:bool=False; supports_camera_motion:bool=False

class PromptBuilderConfiguration(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    phrase_separator:str="; "; include_unspecified:bool=False; exact_count_words:bool=True

class PromptWarning(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    code:str; field:str; message:str

class GeneratedPrompt(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True,allow_inf_nan=False)
    prompt_id:str; provider:PromptProvider; scene_id:str; variant_id:str
    positive_prompt:str; negative_prompt:str; structured_parameters:dict[str,Any]
    warnings:tuple[PromptWarning,...]=(); status:PromptStatus=PromptStatus.VALID

class PromptDependencyMetadata(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    visual_plan_sha256:str=Field(pattern=r"^[a-f0-9]{64}$"); provider:PromptProvider; builder_version:str
    configuration_sha256:str=Field(pattern=r"^[a-f0-9]{64}$")
    capabilities_sha256:str=Field(pattern=r"^[a-f0-9]{64}$")

class PromptBundle(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    schema_version:str=PROMPT_BUNDLE_SCHEMA_VERSION; project_id:str; variant_id:str; provider:PromptProvider
    prompts:tuple[GeneratedPrompt,...]; warnings:tuple[PromptWarning,...]=(); status:PromptStatus
    dependency_metadata:PromptDependencyMetadata; semantic_sha256:str=Field(pattern=r"^[a-f0-9]{64}$")

def default_prompt_capabilities(provider:PromptProvider|str)->PromptCapabilities:
    provider=PromptProvider(provider)
    if provider==PromptProvider.GENERIC_IMAGE:
        return PromptCapabilities(supports_negative_prompt=True,supports_aspect_ratio=True)
    if provider==PromptProvider.GENERIC_VIDEO:
        return PromptCapabilities(supports_negative_prompt=True,supports_duration=True,
            supports_aspect_ratio=True,supports_camera_motion=True)
    return PromptCapabilities(supports_negative_prompt=True,supports_duration=True,
        supports_aspect_ratio=True,supports_reference_images=True,supports_camera_motion=True)

class PromptBuilder:
    def __init__(self,*,builder_version=PROMPT_BUILDER_VERSION,configuration=None):
        self.builder_version=builder_version; self.configuration=configuration or PromptBuilderConfiguration()
    def dependencies(self,*,visual_plan,provider,capabilities):
        return PromptDependencyMetadata(visual_plan_sha256=visual_plan.semantic_sha256,provider=PromptProvider(provider),
            builder_version=self.builder_version,configuration_sha256=semantic_sha256(self.configuration),
            capabilities_sha256=semantic_sha256(capabilities))
    def build_scene_prompt(self,*,scene,variant_id,provider,capabilities):
        scene=VisualScene.model_validate(scene); provider=PromptProvider(provider); capabilities=PromptCapabilities.model_validate(capabilities)
        positive=[]; counts=[]
        exact=self._exact_count(scene)
        for subject in scene.subjects:
            count=subject.count if subject.count is not None else (exact if len(scene.subjects)==1 else None)
            name=subject.display_name or subject.subject_type
            if count is not None:
                positive.append(f"exactly {self._number(count)} {name}")
                counts.append({"subject":subject.subject_type,"count":count,"exact":True})
            else: positive.append(name)
        positive.extend(value.action_type for value in scene.actions)
        positive.extend(self._fields("environment",scene.environment))
        positive.extend(self._fields("style",scene.style))
        positive.extend(self._fields("lighting",scene.lighting))
        positive.extend(self._fields("composition",scene.composition))
        positive.extend(self._fields("camera",scene.camera))
        positive.extend(f"educational constraint {value.key}: {value.value}" for value in scene.educational_constraints)
        continuity=scene.continuity_requirements
        if continuity.required_previous_scene_id: positive.append(f"continuity previous scene: {continuity.required_previous_scene_id}")
        if continuity.required_next_scene_id: positive.append(f"continuity next scene: {continuity.required_next_scene_id}")
        if continuity.shared_subject_ids: positive.append("continuity subjects: "+", ".join(continuity.shared_subject_ids))
        negative=[f"{value.key}: {value.reason}" for value in scene.negative_constraints]
        if any(value.get("exact") for value in counts) and any(value.key=="must_not_show_extra_countable_subjects" for value in scene.educational_constraints):
            negative.append("no additional countable subjects")
        warnings=[]
        if scene.duration_s and not capabilities.supports_duration: warnings.append(self._warning("duration_unsupported","duration"))
        if scene.aspect_ratio and not capabilities.supports_aspect_ratio: warnings.append(self._warning("aspect_ratio_unsupported","aspect_ratio"))
        if scene.camera.movement_intent!="unspecified" and not capabilities.supports_camera_motion: warnings.append(self._warning("camera_motion_unsupported","camera.movement_intent"))
        if negative and not capabilities.supports_negative_prompt: warnings.append(self._warning("negative_prompt_unsupported","negative_prompt"))
        structured={"aspect_ratio":scene.aspect_ratio.value,"duration":scene.duration_s,
            "camera":scene.camera.model_dump(mode="json"),"lighting":scene.lighting.model_dump(mode="json"),
            "style":scene.style.model_dump(mode="json"),"subjects":[x.model_dump(mode="json") for x in scene.subjects],
            "counts":counts,"educational_constraints":[x.model_dump(mode="json") for x in scene.educational_constraints]}
        status=self._status(scene.status,warnings)
        return GeneratedPrompt(prompt_id=f"{provider.value}-{scene.visual_scene_id}",provider=provider,
            scene_id=scene.visual_scene_id,variant_id=variant_id,positive_prompt=self.configuration.phrase_separator.join(positive),
            negative_prompt=self.configuration.phrase_separator.join(negative),structured_parameters=structured,
            warnings=tuple(warnings),status=status)
    def build_prompt_bundle(self,*,visual_plan,provider,capabilities):
        plan=VisualPlan.model_validate(visual_plan); provider=PromptProvider(provider); capabilities=PromptCapabilities.model_validate(capabilities)
        dependencies=self.dependencies(visual_plan=plan,provider=provider,capabilities=capabilities)
        prompts=tuple(self.build_scene_prompt(scene=x,variant_id=plan.audio_variant_id,provider=provider,capabilities=capabilities) for x in plan.scenes)
        warnings=tuple(w for prompt in prompts for w in prompt.warnings); status=self._status(plan.status,warnings)
        core={"schema_version":PROMPT_BUNDLE_SCHEMA_VERSION,"project_id":plan.project_id,"variant_id":plan.audio_variant_id,
            "provider":provider.value,"prompts":[x.model_dump(mode="json") for x in prompts],
            "warnings":[x.model_dump(mode="json") for x in warnings],"status":status.value,
            "dependency_metadata":dependencies.model_dump(mode="json")}
        return PromptBundle(**core,semantic_sha256=semantic_sha256(core))
    def _fields(self,prefix,value):
        result=[]
        for key,item in value.model_dump(mode="json").items():
            if item in (None,"unspecified",[],()): continue
            if isinstance(item,list): item=", ".join(str(x) for x in item)
            result.append(f"{prefix} {key}: {item}")
        return result
    @staticmethod
    def _exact_count(scene):
        for value in scene.educational_constraints:
            if value.key in {"must_show_count","exact_count","count"}:
                match=re.search(r"\d+",str(value.value))
                if match: return int(match.group())
        return None
    def _number(self,value):
        words={1:"one",2:"two",3:"three",4:"four",5:"five",6:"six",7:"seven",8:"eight",9:"nine",10:"ten"}
        return words.get(value,str(value)) if self.configuration.exact_count_words else str(value)
    @staticmethod
    def _warning(code,field): return PromptWarning(code=code,field=field,message=f"Provider capability does not support {field}; information remains in structured parameters.")
    @staticmethod
    def _status(status,warnings):
        value=status.value if isinstance(status,Enum) else str(status)
        if value=="invalid": return PromptStatus.INVALID
        if value=="review_required": return PromptStatus.REVIEW_REQUIRED
        return PromptStatus.VALID_WITH_WARNINGS if warnings or value=="valid_with_warnings" else PromptStatus.VALID

def prompt_bundle_to_dict(bundle): return PromptBundle.model_validate(bundle).model_dump(mode="json")
def write_prompt_bundle(path,bundle):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True); part=path.with_suffix(path.suffix+".part")
    try:
        part.write_text(json.dumps(prompt_bundle_to_dict(bundle),ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8")
        with part.open("r+b") as stream: os.fsync(stream.fileno())
        os.replace(part,path)
    except OSError as error: raise PromptPersistenceError("Prompt bundle could not be persisted.") from error
    finally: part.unlink(missing_ok=True)
def read_prompt_bundle(path):
    try: return PromptBundle.model_validate_json(Path(path).read_text(encoding="utf-8"))
    except Exception as error: raise PromptPersistenceError("Prompt bundle is invalid.") from error

class PromptRepository:
    def __init__(self,directory): self.directory=Path(directory)
    def path(self,variant_id,provider): return self.directory/variant_id/f"{PromptProvider(provider).value.replace('_','-')}.json"
    def resolve_or_build(self,*,visual_plan,builder,provider,capabilities):
        path=self.path(visual_plan.audio_variant_id,provider)
        expected=builder.dependencies(visual_plan=visual_plan,provider=provider,capabilities=capabilities)
        existing=read_prompt_bundle(path) if path.is_file() else None
        if existing is not None and existing.dependency_metadata==expected: return existing,True
        value=builder.build_prompt_bundle(visual_plan=visual_plan,provider=provider,capabilities=capabilities)
        write_prompt_bundle(path,value); return value,False
