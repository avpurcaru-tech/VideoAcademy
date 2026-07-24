"""Provider-neutral visual intent projection from ScenePlan (Sprint 17.2)."""
import json,os
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel,ConfigDict,Field,model_validator

from app.scene_planning import ScenePlan,ScenePlanStatus,SceneType,semantic_sha256

VISUAL_PLAN_SCHEMA_VERSION="1.0"
VISUAL_PLANNER_VERSION="17.2.0"

class VisualPlanningError(RuntimeError): pass
class VisualPlanPersistenceError(VisualPlanningError): pass
class VisualPlanStatus(str,Enum):
    VALID="valid"; VALID_WITH_WARNINGS="valid_with_warnings"; REVIEW_REQUIRED="review_required"; INVALID="invalid"
class ShotSize(str,Enum): WIDE="wide"; MEDIUM="medium"; CLOSE="close"; DETAIL="detail"; UNSPECIFIED="unspecified"
class CameraAngle(str,Enum):
    EYE_LEVEL="eye_level"; HIGH_ANGLE="high_angle"; LOW_ANGLE="low_angle"; OVERHEAD="overhead"; UNSPECIFIED="unspecified"
class AspectRatio(str,Enum): LANDSCAPE_16_9="16:9"; PORTRAIT_9_16="9:16"; SQUARE_1_1="1:1"

class VisualPlanningConfiguration(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    default_aspect_ratio:AspectRatio=AspectRatio.LANDSCAPE_16_9
    default_target_age_group:str="2_to_5"; default_medium:str="3d_animation"
    default_render_style:str="friendly_stylized"; default_shape_language:str="soft_rounded_shapes"
    default_complexity:str="simple"; default_readability:str="high"

class VisualSubject(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    subject_type:str; count:int|None=Field(default=None,gt=0); importance:str="unspecified"
    educational_role:str="unspecified"; source_subject_id:str|None=None; display_name:str|None=None
class VisualAction(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    action_type:str; subject_reference:str|None=None; object_reference:str|None=None; intensity:str="unspecified"
    source_action_id:str|None=None
class VisualEnvironment(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    location:str="unspecified"; weather:str="unspecified"; time_of_day:str="unspecified"
    ground_type:str="unspecified"; background_type:str="unspecified"
class VisualCamera(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    shot_size:ShotSize=ShotSize.UNSPECIFIED; angle:CameraAngle=CameraAngle.UNSPECIFIED
    movement_intent:str="unspecified"; focus_target:str="unspecified"
class VisualComposition(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    foreground:tuple[str,...]=(); midground:tuple[str,...]=(); background:tuple[str,...]=()
    focus_subject:str="unspecified"; depth:str="unspecified"; framing:str="unspecified"
class VisualLighting(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    lighting_type:str="unspecified"; brightness:str="unspecified"; temperature:str="unspecified"; direction:str="unspecified"
class VisualStyle(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    medium:str; render_style:str; shape_language:str; complexity:str; readability:str; target_age_group:str
class VisualPalette(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    palette_intent:str="unspecified"; saturation:str="unspecified"; contrast:str="unspecified"; background_complexity:str="unspecified"
class VisualConstraint(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    constraint_type:str; key:str; value:Any; required:bool=True
class VisualNegativeConstraint(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    constraint_type:str; key:str; value:Any; reason:str
class CharacterWardrobeOverride(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    character_id:str; top:str|None=None; bottom:str|None=None; shoes:str|None=None; accessories:tuple[str,...]|None=None
class VisualContinuityRequirement(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    required_previous_scene_id:str|None=None; required_next_scene_id:str|None=None
    shared_subject_ids:tuple[str,...]=(); shared_object_ids:tuple[str,...]=()
class VisualPlanWarning(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    code:str; source_id:str|None=None; path:str

class VisualScene(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True,allow_inf_nan=False)
    visual_scene_id:str; source_scene_id:str; ordinal:int=Field(ge=0)
    start_s:float=Field(ge=0); end_s:float=Field(gt=0); duration_s:float=Field(gt=0)
    aspect_ratio:AspectRatio; subjects:tuple[VisualSubject,...]=(); actions:tuple[VisualAction,...]=()
    character_ids:tuple[str,...]=()
    character_wardrobe_overrides:tuple[CharacterWardrobeOverride,...]=()
    environment:VisualEnvironment; camera:VisualCamera; composition:VisualComposition; lighting:VisualLighting
    style:VisualStyle; palette:VisualPalette; educational_constraints:tuple[VisualConstraint,...]=()
    positive_constraints:tuple[VisualConstraint,...]=(); negative_constraints:tuple[VisualNegativeConstraint,...]=()
    continuity_requirements:VisualContinuityRequirement; source_references:tuple[dict[str,Any],...]
    source_texts:tuple[str,...]=()
    status:VisualPlanStatus; warnings:tuple[VisualPlanWarning,...]=()
    @model_validator(mode="after")
    def timing(self):
        if self.end_s<=self.start_s or abs(self.duration_s-(self.end_s-self.start_s))>.01:
            raise ValueError("Visual scene timing is inconsistent.")
        return self

class VisualPlanDependencyMetadata(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    source_scene_plan_sha256:str=Field(pattern=r"^[a-f0-9]{64}$"); generator_version:str
    configuration_sha256:str=Field(pattern=r"^[a-f0-9]{64}$"); global_style_sha256:str=Field(pattern=r"^[a-f0-9]{64}$")
    aspect_ratio:AspectRatio
    character_dependency_sha256:str=Field(default="0"*64,pattern=r"^[a-f0-9]{64}$")
class VisualPlan(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    schema_version:str=VISUAL_PLAN_SCHEMA_VERSION; project_id:str; audio_variant_id:str
    source_scene_plan_sha256:str=Field(pattern=r"^[a-f0-9]{64}$"); generator_version:str
    aspect_ratio:AspectRatio; global_style:VisualStyle; scenes:tuple[VisualScene,...]
    status:VisualPlanStatus; warnings:tuple[VisualPlanWarning,...]=()
    dependency_metadata:VisualPlanDependencyMetadata; semantic_sha256:str=Field(pattern=r"^[a-f0-9]{64}$")
    @model_validator(mode="after")
    def projection(self):
        if any(value.ordinal!=index for index,value in enumerate(self.scenes)): raise ValueError("Visual scene order is invalid.")
        return self

def default_visual_style(configuration=None):
    value=configuration or VisualPlanningConfiguration()
    return VisualStyle(medium=value.default_medium,render_style=value.default_render_style,
        shape_language=value.default_shape_language,complexity=value.default_complexity,
        readability=value.default_readability,target_age_group=value.default_target_age_group)

class ProviderNeutralVisualPlanner:
    def __init__(self,*,generator_version=VISUAL_PLANNER_VERSION,configuration=None):
        self.generator_version=generator_version; self.configuration=configuration or VisualPlanningConfiguration()
    def dependencies(self,*,scene_plan,global_style,aspect_ratio,character_registry=None):
        ratio=AspectRatio(aspect_ratio)
        character_ids=self._character_ids(scene_plan,character_registry)
        return VisualPlanDependencyMetadata(source_scene_plan_sha256=scene_plan.semantic_sha256,
            generator_version=self.generator_version,configuration_sha256=semantic_sha256(self.configuration),
            global_style_sha256=semantic_sha256(global_style),aspect_ratio=ratio,
            character_dependency_sha256=(character_registry.dependency_sha256(character_ids) if character_registry else "0"*64))
    def plan(self,*,scene_plan,global_style,aspect_ratio,character_registry=None):
        scene_plan=ScenePlan.model_validate(scene_plan); global_style=VisualStyle.model_validate(global_style); ratio=AspectRatio(aspect_ratio)
        dependencies=self.dependencies(scene_plan=scene_plan,global_style=global_style,aspect_ratio=ratio,character_registry=character_registry)
        scenes=[]
        for index,source in enumerate(scene_plan.scenes):
            subjects=tuple(VisualSubject(subject_type=value.subject_type,source_subject_id=value.subject_id,
                display_name=value.display_name) for value in source.subjects)
            character_ids=tuple(value for subject in source.subjects for value in self._resolve_character(subject,character_registry)[:1] if value)
            actions=tuple(VisualAction(action_type=value.description,source_action_id=value.action_id) for value in source.actions)
            instrumental=source.scene_type in {SceneType.INSTRUMENTAL_INTRO,SceneType.INSTRUMENTAL_BREAK,SceneType.INSTRUMENTAL_OUTRO}
            if instrumental: subjects=(); actions=(); character_ids=()
            constraints=(() if instrumental else tuple(VisualConstraint(constraint_type=value.constraint_type,
                key=value.constraint_type,value=value.value,required=True) for value in source.educational_constraints))
            negatives=[]
            for value in constraints:
                enabled=value.value is True or (isinstance(value.value,str) and value.value.casefold()=="true")
                if value.key=="must_not_show_extra_countable_subjects" and enabled:
                    negatives.append(VisualNegativeConstraint(constraint_type="count",key="extra_countable_subjects",
                        value=False,reason="Preserve exact educational count"))
            focus=subjects[0].source_subject_id if len(subjects)==1 else "unspecified"
            previous=scene_plan.scenes[index-1].scene_id if index else None
            following=scene_plan.scenes[index+1].scene_id if index+1<len(scene_plan.scenes) else None
            previous_ids={value.subject_id for value in scene_plan.scenes[index-1].subjects} if index else set()
            next_ids={value.subject_id for value in scene_plan.scenes[index+1].subjects} if following else set()
            current_ids={value.subject_id for value in source.subjects}
            shared=tuple(sorted(current_ids&(previous_ids|next_ids)))
            warnings=tuple(VisualPlanWarning(code=value.code,source_id=value.source_id,path=value.path) for value in source.warnings)
            status=self._status(source.status,warnings)
            scenes.append(VisualScene(visual_scene_id=f"visual-{source.scene_id}",source_scene_id=source.scene_id,
                ordinal=source.ordinal,start_s=source.start_s,end_s=source.end_s,duration_s=source.duration_s,
                aspect_ratio=ratio,subjects=subjects,actions=actions,
                character_ids=character_ids,
                environment=(VisualEnvironment() if instrumental else VisualEnvironment(location=source.environment.location,
                    weather=source.environment.weather,time_of_day=source.environment.time_of_day)),
                camera=VisualCamera(),composition=VisualComposition(focus_subject=focus),lighting=VisualLighting(),
                style=global_style,palette=VisualPalette(),educational_constraints=constraints,
                positive_constraints=constraints,negative_constraints=tuple(negatives),
                continuity_requirements=VisualContinuityRequirement(required_previous_scene_id=previous,
                    required_next_scene_id=following,shared_subject_ids=shared),
                source_references=tuple(value.model_dump(mode="json") for value in source.source_references),
                source_texts=source.source_texts,status=status,warnings=warnings))
        warnings=tuple(VisualPlanWarning(code=value.code,source_id=value.source_id,path=value.path) for value in scene_plan.warnings)
        status=self._status(scene_plan.status,warnings)
        core={"schema_version":VISUAL_PLAN_SCHEMA_VERSION,"project_id":scene_plan.project_id,
            "audio_variant_id":scene_plan.audio_variant_id,"source_scene_plan_sha256":scene_plan.semantic_sha256,
            "generator_version":self.generator_version,"aspect_ratio":ratio.value,"global_style":global_style.model_dump(mode="json"),
            "scenes":[value.model_dump(mode="json") for value in scenes],"status":status.value,
            "warnings":[value.model_dump(mode="json") for value in warnings],"dependency_metadata":dependencies.model_dump(mode="json")}
        return VisualPlan(**core,semantic_sha256=semantic_sha256(core))
    @staticmethod
    def _resolve_character(subject,registry):
        if registry is None: return (None,None)
        for value in (subject.subject_id,subject.display_name):
            if value:
                character_id,warning=registry.resolve_alias(value)
                if character_id: return character_id,warning
        return None,None
    def _character_ids(self,scene_plan,registry):
        if registry is None: return ()
        return tuple(sorted({value for scene in scene_plan.scenes for subject in scene.subjects
            for value in self._resolve_character(subject,registry)[:1] if value}))
    @staticmethod
    def _status(status,warnings):
        value=status.value if isinstance(status,Enum) else str(status)
        if value=="invalid": return VisualPlanStatus.INVALID
        if value=="review_required": return VisualPlanStatus.REVIEW_REQUIRED
        if warnings or value=="valid_with_warnings": return VisualPlanStatus.VALID_WITH_WARNINGS
        return VisualPlanStatus.VALID

def visual_plan_to_dict(plan): return VisualPlan.model_validate(plan).model_dump(mode="json")
def visual_plan_from_dict(payload): return VisualPlan.model_validate(payload)
def write_visual_plan(path,plan):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True); part=path.with_suffix(path.suffix+".part")
    try:
        part.write_text(json.dumps(visual_plan_to_dict(plan),ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8")
        with part.open("r+b") as stream: os.fsync(stream.fileno())
        os.replace(part,path)
    except OSError as error: raise VisualPlanPersistenceError("Visual plan could not be persisted.") from error
    finally: part.unlink(missing_ok=True)
def read_visual_plan(path):
    try: return VisualPlan.model_validate_json(Path(path).read_text(encoding="utf-8"))
    except Exception as error: raise VisualPlanPersistenceError("Visual plan is invalid.") from error

class VisualPlanRepository:
    def __init__(self,directory): self.directory=Path(directory)
    def path(self,variant_id): return self.directory/f"visual-plan-{variant_id}.json"
    def resolve_or_build(self,*,scene_plan,planner,global_style,aspect_ratio,character_registry=None):
        path=self.path(scene_plan.audio_variant_id); expected=planner.dependencies(scene_plan=scene_plan,global_style=global_style,aspect_ratio=aspect_ratio,character_registry=character_registry)
        existing=read_visual_plan(path) if path.is_file() else None
        if existing is not None and existing.dependency_metadata==expected: return existing,True
        value=planner.plan(scene_plan=scene_plan,global_style=global_style,aspect_ratio=aspect_ratio,character_registry=character_registry); write_visual_plan(path,value); return value,False
