"""Deterministic provider-neutral semantic scene planning (Sprint 17.1)."""
import hashlib,json,math,os,re
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel,ConfigDict,Field,model_validator


SCENE_PLAN_SCHEMA_VERSION="1.0"
SCENE_PLANNER_VERSION="17.1.0"
SCENE_TIMING_TOLERANCE_SECONDS=.01


class ScenePlanningError(RuntimeError): pass
class ScenePlanInvalidError(ScenePlanningError): pass
class ScenePlanPersistenceError(ScenePlanningError): pass

class ScenePlanStatus(str,Enum):
    VALID="valid"; VALID_WITH_WARNINGS="valid_with_warnings"; REVIEW_REQUIRED="review_required"; INVALID="invalid"
class SceneType(str,Enum):
    VOCAL="vocal"; INSTRUMENTAL_INTRO="instrumental_intro"; INSTRUMENTAL_BREAK="instrumental_break"
    INSTRUMENTAL_OUTRO="instrumental_outro"; TRANSITION="transition"
class SceneSourceType(str,Enum):
    LYRICS_LINE="lyrics_line"; LYRICS_SECTION="lyrics_section"; ALIGNMENT_LINE="alignment_line"
    INSTRUMENTAL_SECTION="instrumental_section"; STORY_SEGMENT="story_segment"; PROJECT_METADATA="project_metadata"


class ScenePlanningThresholds(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True,allow_inf_nan=False)
    timing_tolerance_seconds:float=Field(default=SCENE_TIMING_TOLERANCE_SECONDS,ge=0)
    group_vocal_lines:bool=False

class ScenePlanWarning(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    code:str; source_id:str|None=None; path:str

class SceneTiming(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True,allow_inf_nan=False)
    start_s:float=Field(ge=0); end_s:float=Field(gt=0); duration_s:float=Field(gt=0)
    @model_validator(mode="after")
    def consistent(self):
        if self.end_s<=self.start_s: raise ValueError("Scene timing interval is empty.")
        if abs(self.duration_s-(self.end_s-self.start_s))>SCENE_TIMING_TOLERANCE_SECONDS:
            raise ValueError("Scene duration differs from its interval.")
        return self

class SceneSourceReference(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    source_type:SceneSourceType; source_id:str; source_ordinal:int|None=Field(default=None,ge=0)
    source_sha256:str|None=Field(default=None,pattern=r"^[a-f0-9]{64}$")

class SceneSubject(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    subject_id:str; subject_type:str="unknown"; display_name:str|None=None
class SceneAction(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    action_id:str; description:str
class SceneEnvironment(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    location:str="unspecified"; time_of_day:str="unspecified"; weather:str="unspecified"
class SceneEducationalConstraint(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    constraint_type:str; value:str

class PlannedScene(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    scene_id:str; ordinal:int=Field(ge=0); scene_type:SceneType; timing:SceneTiming
    source_line_ids:tuple[str,...]=(); source_section_ids:tuple[str,...]=()
    source_texts:tuple[str,...]=()
    source_references:tuple[SceneSourceReference,...]
    subjects:tuple[SceneSubject,...]=(); actions:tuple[SceneAction,...]=()
    environment:SceneEnvironment=SceneEnvironment(); mood:str="unspecified"
    educational_constraints:tuple[SceneEducationalConstraint,...]=()
    continuity_hints:tuple[str,...]=(); status:ScenePlanStatus=ScenePlanStatus.VALID
    warnings:tuple[ScenePlanWarning,...]=()
    @property
    def start_s(self): return self.timing.start_s
    @property
    def end_s(self): return self.timing.end_s
    @property
    def duration_s(self): return self.timing.duration_s

class ScenePlanDependencyMetadata(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    audio_variant_id:str; audio_sha256:str=Field(pattern=r"^[a-f0-9]{64}$")
    alignment_sha256:str=Field(pattern=r"^[a-f0-9]{64}$"); lyrics_sha256:str=Field(pattern=r"^[a-f0-9]{64}$")
    semantic_metadata_sha256:str=Field(pattern=r"^[a-f0-9]{64}$"); configuration_sha256:str=Field(pattern=r"^[a-f0-9]{64}$")
    audio_duration_s:float=Field(gt=0); planner_version:str

class ScenePlan(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    schema_version:str=SCENE_PLAN_SCHEMA_VERSION; project_id:str; audio_variant_id:str
    source_alignment_sha256:str=Field(pattern=r"^[a-f0-9]{64}$")
    source_lyrics_sha256:str=Field(pattern=r"^[a-f0-9]{64}$")
    audio_sha256:str=Field(pattern=r"^[a-f0-9]{64}$"); audio_duration_s:float=Field(gt=0)
    planner_version:str; status:ScenePlanStatus; scenes:tuple[PlannedScene,...]
    unplanned_line_ids:tuple[str,...]=(); warnings:tuple[ScenePlanWarning,...]=()
    dependency_metadata:ScenePlanDependencyMetadata; semantic_sha256:str=Field(pattern=r"^[a-f0-9]{64}$")
    @model_validator(mode="after")
    def valid_timeline(self):
        tolerance=SCENE_TIMING_TOLERANCE_SECONDS
        for index,scene in enumerate(self.scenes):
            if scene.ordinal!=index: raise ValueError("Scene ordinals must be contiguous.")
            if scene.end_s>self.audio_duration_s+tolerance: raise ValueError("Scene exceeds audio duration.")
        for previous,current in zip(self.scenes,self.scenes[1:]):
            if current.start_s<previous.end_s-tolerance: raise ValueError("Scene intervals overlap.")
        return self


def _canonical(value:Any):
    if isinstance(value,BaseModel): value=value.model_dump(mode="json",exclude={"created_at"})
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False)
def semantic_sha256(value): return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()
def alignment_semantic_sha256(alignment): return semantic_sha256(alignment.model_dump(mode="json",exclude={"created_at"}))
def lyrics_semantic_sha256(lyrics): return semantic_sha256(lyrics)

def scene_plan_to_dict(plan:ScenePlan)->dict[str,object]:
    return ScenePlan.model_validate(plan).model_dump(mode="json")

def write_scene_plan(path:Path,plan:ScenePlan)->None:
    """Atomic, stable UTF-8 serialization; paths are supplied by the caller and never embedded."""
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True); part=path.with_suffix(path.suffix+".part")
    try:
        part.write_text(json.dumps(scene_plan_to_dict(plan),ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8")
        with part.open("r+b") as stream: os.fsync(stream.fileno())
        os.replace(part,path)
    except OSError as error: raise ScenePlanPersistenceError("Scene plan could not be persisted.") from error
    finally: part.unlink(missing_ok=True)

def read_scene_plan(path:Path)->ScenePlan:
    try: return ScenePlan.model_validate_json(Path(path).read_text(encoding="utf-8"))
    except Exception as error: raise ScenePlanPersistenceError("Scene plan is invalid.") from error


class SemanticScenePlanner:
    def __init__(self,thresholds=None,planner_version=SCENE_PLANNER_VERSION):
        self.thresholds=thresholds or ScenePlanningThresholds(); self.planner_version=planner_version

    def dependencies(self,alignment,lyrics,storyboard=None,music_timeline=None):
        semantic=self._semantic_metadata(storyboard,music_timeline)
        return ScenePlanDependencyMetadata(audio_variant_id=alignment.variant_id,audio_sha256=alignment.audio_sha256,
            alignment_sha256=alignment_semantic_sha256(alignment),lyrics_sha256=lyrics_semantic_sha256(lyrics),
            semantic_metadata_sha256=semantic_sha256(semantic),configuration_sha256=semantic_sha256(self.thresholds),
            audio_duration_s=alignment.audio_duration_seconds,planner_version=self.planner_version)

    def plan(self,project_id,alignment,lyrics,storyboard=None,music_timeline=None):
        dependencies=self.dependencies(alignment,lyrics,storyboard,music_timeline)
        lyric_lines={line.line_id:(line,section,index) for section in lyrics.sections
            for index,line in enumerate(section.lines)}
        storyboard_sections={value.section_id:value for value in getattr(storyboard,"sections",())}
        timeline_segments=tuple(getattr(music_timeline,"segments",()))
        candidates=[]
        for aligned in alignment.lines:
            source=lyric_lines.get(aligned.source_lyrics_line_id)
            if source is None: raise ScenePlanInvalidError("Aligned line references an unknown lyrics line.")
            lyric_line,lyrics_section,line_ordinal=source
            story_segment=next((value for value in timeline_segments
                if value.start_seconds-self.thresholds.timing_tolerance_seconds<=aligned.start_seconds<value.end_seconds+self.thresholds.timing_tolerance_seconds),None)
            story=storyboard_sections.get(story_segment.storyboard_section_id) if story_segment else None
            references=[SceneSourceReference(source_type="lyrics_line",source_id=lyric_line.line_id,
                source_ordinal=line_ordinal,source_sha256=semantic_sha256(lyric_line)),
                SceneSourceReference(source_type="lyrics_section",source_id=lyrics_section.section_id,
                    source_ordinal=lyrics_section.order,source_sha256=semantic_sha256(lyrics_section)),
                SceneSourceReference(source_type="alignment_line",source_id=aligned.line_id,
                    source_sha256=semantic_sha256(aligned))]
            if story_segment:
                references.append(SceneSourceReference(source_type="story_segment",source_id=story_segment.storyboard_section_id,
                    source_sha256=semantic_sha256(story_segment)))
            subjects=tuple(SceneSubject(subject_id=value,display_name=value) for value in getattr(story,"characters",()))
            actions=tuple(SceneAction(action_id=f"{story.section_id}-action-{index:02d}",description=value)
                for index,value in enumerate(getattr(story,"actions",()),1))
            environment=SceneEnvironment(location=getattr(story,"environment","unspecified") or "unspecified")
            constraints=()
            visual_goal=getattr(story,"visual_goal",None)
            if visual_goal: constraints=(SceneEducationalConstraint(constraint_type="visual_goal",value=visual_goal),)
            timing=SceneTiming(start_s=aligned.start_seconds,end_s=aligned.end_seconds,
                duration_s=aligned.end_seconds-aligned.start_seconds)
            candidates.append((timing.start_s,"vocal",aligned,PlannedScene(scene_id=self._scene_id(alignment.variant_id,"vocal",aligned.line_id),
                ordinal=0,scene_type="vocal",timing=timing,source_line_ids=(lyric_line.line_id,),
                source_section_ids=(lyrics_section.section_id,),source_texts=(lyric_line.text,),
                source_references=tuple(references),subjects=subjects,
                actions=actions,environment=environment,mood=getattr(story,"emotion","unspecified") or "unspecified",
                educational_constraints=constraints)))
        for section in alignment.sections:
            if section.section_type not in {"instrumental_intro","instrumental_break","instrumental_outro"}: continue
            timing=SceneTiming(start_s=section.start_seconds,end_s=section.end_seconds,duration_s=section.end_seconds-section.start_seconds)
            reference=SceneSourceReference(source_type="instrumental_section",source_id=section.section_id,
                source_sha256=semantic_sha256(section))
            candidates.append((timing.start_s,section.section_type,section,PlannedScene(
                scene_id=self._scene_id(alignment.variant_id,section.section_type,section.section_id),ordinal=0,
                scene_type=section.section_type,timing=timing,source_section_ids=(section.section_id,),
                source_references=(reference,),environment=SceneEnvironment(),mood="unspecified")))
        candidates.sort(key=lambda value:(value[0],value[1],value[3].scene_id))
        scenes=tuple(value[3].model_copy(update={"ordinal":index}) for index,value in enumerate(candidates))
        warnings=tuple(ScenePlanWarning(code="unmapped_lyrics_line",source_id=line_id,
            path=f"lyrics.lines[{line_id}]") for line_id in alignment.unmatched_lyrics_line_ids)
        status=(ScenePlanStatus.REVIEW_REQUIRED if alignment.status.value=="review_required" else
            ScenePlanStatus.VALID_WITH_WARNINGS if warnings or alignment.status.value=="valid_with_warnings" else ScenePlanStatus.VALID)
        core={"schema_version":SCENE_PLAN_SCHEMA_VERSION,"project_id":project_id,"audio_variant_id":alignment.variant_id,
            "source_alignment_sha256":dependencies.alignment_sha256,"source_lyrics_sha256":dependencies.lyrics_sha256,
            "audio_sha256":alignment.audio_sha256,"audio_duration_s":alignment.audio_duration_seconds,
            "planner_version":self.planner_version,"status":status.value,"scenes":[value.model_dump(mode="json") for value in scenes],
            "unplanned_line_ids":list(alignment.unmatched_lyrics_line_ids),"warnings":[value.model_dump(mode="json") for value in warnings],
            "dependency_metadata":dependencies.model_dump(mode="json")}
        return ScenePlan(**core,semantic_sha256=semantic_sha256(core))

    @staticmethod
    def _scene_id(variant,kind,source_id):
        safe=re.sub(r"[^a-z0-9_-]+","-",source_id.casefold()).strip("-") or "source"
        digest=hashlib.sha256(f"{variant}\0{kind}\0{source_id}".encode("utf-8")).hexdigest()[:10]
        return f"{variant}-{kind}-{safe[:60]}-{digest}"
    @staticmethod
    def _semantic_metadata(storyboard,music_timeline):
        sections=[]
        for value in getattr(storyboard,"sections",()):
            sections.append({key:getattr(value,key,None) for key in
                ("section_id","characters","actions","environment","emotion","visual_goal")})
        timeline=[value.model_dump(mode="json") for value in getattr(music_timeline,"segments",())]
        return {"storyboard_id":getattr(storyboard,"storyboard_id",None),"sections":sections,"timeline":timeline}


class ScenePlanRepository:
    def __init__(self,directory): self.directory=Path(directory)
    def path(self,variant_id): return self.directory/f"scene-plan-{variant_id}.json"
    def load(self,variant_id):
        path=self.path(variant_id)
        if not path.is_file(): return None
        return read_scene_plan(path)
    def resolve_or_build(self,project_id,alignment,lyrics,planner,storyboard=None,music_timeline=None):
        expected=planner.dependencies(alignment,lyrics,storyboard,music_timeline); existing=self.load(alignment.variant_id)
        if existing is not None and existing.dependency_metadata==expected: return existing,True
        value=planner.plan(project_id,alignment,lyrics,storyboard,music_timeline); self.save(value); return value,False
    def save(self,value):
        write_scene_plan(self.path(value.audio_variant_id),value)
