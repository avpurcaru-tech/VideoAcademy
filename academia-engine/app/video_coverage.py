import math
import unicodedata
from enum import Enum

from pydantic import BaseModel,ConfigDict,Field,model_validator

from app.storyboard import CreativeStoryboard
from app.music_timeline import MusicTimeline


class VideoCoveragePolicy(str,Enum):
    FULL_GENERATION="full_generation"
    BALANCED="balanced"
    BUDGET="budget"


class VideoProviderCapabilities(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    provider_name: str
    supported_clip_durations: tuple[int,...]=Field(min_length=1)
    selected_clip_duration: int=Field(gt=0)
    maximum_clips_per_production: int|None=Field(default=None,gt=0)
    supports_reference_images: bool=False
    supports_multiple_references: bool=False
    cost_per_generated_second: float|None=Field(default=None,ge=0)

    @model_validator(mode="after")
    def selected_supported(self):
        if self.selected_clip_duration not in self.supported_clip_durations:
            raise ValueError("Selected provider clip duration is unsupported.")
        return self


class VideoCoverageConfiguration(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    policy: VideoCoveragePolicy=VideoCoveragePolicy.BALANCED
    balanced_unique_coverage_ratio: float=Field(default=.65,gt=0,le=1)
    maximum_scene_count: int|None=Field(default=None,gt=0)
    maximum_generation_budget: float|None=Field(default=None,ge=0)
    selected_variant_id: str|None=None


class CoverageShot(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    shot_id: str
    source_storyboard_section_id: str
    semantic_purpose: str
    action_variation: str
    camera_variation: str
    duration_seconds: float=Field(gt=0)
    recurring_character_ids: tuple[str,...]=()


class CoverageUsage(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    order: int=Field(ge=1)
    shot_id: str
    source_storyboard_section_id: str
    duration_seconds: float=Field(gt=0)
    reused: bool=False
    reused_from_shot_id: str|None=None


class VariantCoveragePlan(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    variant_id: str
    audio_duration_seconds: float=Field(gt=0)
    usages: tuple[CoverageUsage,...]=Field(min_length=1)
    final_trim_seconds: float=Field(ge=0)
    coverage_seconds: float=Field(gt=0)
    coverage_valid: bool


class VideoCoveragePlan(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    coverage_duration_seconds: float=Field(gt=0)
    provider_capabilities: VideoProviderCapabilities
    policy: VideoCoveragePolicy
    original_section_count: int=Field(gt=0)
    unique_scene_count: int=Field(gt=0)
    derived_or_reused_scene_count: int=Field(ge=0)
    total_timeline_coverage_seconds: float=Field(gt=0)
    unique_shots: tuple[CoverageShot,...]=Field(min_length=1)
    shared_usage_plan: tuple[CoverageUsage,...]=Field(min_length=1)
    variant_plans: tuple[VariantCoveragePlan,...]=Field(min_length=1)
    estimated_generated_seconds: float=Field(gt=0)
    estimated_provider_cost: float|None=None
    maximum_allowed_cost: float|None=None
    confirmation_required: bool=False


class VideoCoveragePlanningError(RuntimeError): pass


class VideoCoveragePlanner:
    _CAMERAS=("wide establishing","medium tracking","close detail","low-angle movement","gentle orbit")

    def plan(self,audio_variant_durations:dict[str,float],storyboard:CreativeStoryboard,
             capabilities:VideoProviderCapabilities,configuration:VideoCoverageConfiguration|None=None,
             timelines:dict[str,MusicTimeline]|None=None)->VideoCoveragePlan:
        configuration=configuration or VideoCoverageConfiguration()
        if not audio_variant_durations: raise VideoCoveragePlanningError("At least one probed audio duration is required.")
        if any(value<=0 for value in audio_variant_durations.values()): raise VideoCoveragePlanningError("Audio durations must be positive.")
        if configuration.selected_variant_id:
            if configuration.selected_variant_id not in audio_variant_durations: raise VideoCoveragePlanningError("Selected audio variant is unavailable.")
            coverage=audio_variant_durations[configuration.selected_variant_id]
        else: coverage=max(audio_variant_durations.values())
        clip=capabilities.selected_clip_duration; slots=math.ceil(coverage/clip)
        if configuration.policy==VideoCoveragePolicy.FULL_GENERATION: unique=slots
        elif configuration.policy==VideoCoveragePolicy.BALANCED: unique=max(min(len(storyboard.sections),slots),math.ceil(slots*configuration.balanced_unique_coverage_ratio))
        else: unique=slots
        cost_rate=capabilities.cost_per_generated_second
        if configuration.policy==VideoCoveragePolicy.BUDGET and configuration.maximum_generation_budget is not None and cost_rate:
            unique=min(unique,math.floor(configuration.maximum_generation_budget/(clip*cost_rate)))
        if capabilities.maximum_clips_per_production is not None and unique>capabilities.maximum_clips_per_production:
            if configuration.policy==VideoCoveragePolicy.FULL_GENERATION:
                raise VideoCoveragePlanningError("Provider maximum clips cannot cover the requested duration.")
            unique=capabilities.maximum_clips_per_production
        if configuration.maximum_scene_count is not None and configuration.policy!=VideoCoveragePolicy.FULL_GENERATION:
            unique=min(unique,configuration.maximum_scene_count)
        if unique<1: raise VideoCoveragePlanningError("Generation budget cannot fund one provider clip.")
        if slots>=len(storyboard.sections) and unique<len(storyboard.sections):
            raise VideoCoveragePlanningError("Generation limits cannot represent every storyboard section.")
        weights=self._section_weights(storyboard,timelines,audio_variant_durations)
        allocation=self._allocate(unique,weights)
        shots=[]
        for section,count in zip(storyboard.sections,allocation,strict=True):
            for index in range(count):
                shots.append(CoverageShot(shot_id=f"shot-{len(shots)+1:04d}",source_storyboard_section_id=section.section_id,
                    semantic_purpose=f"{section.section_type}: {section.visual_goal}"[:500],
                    action_variation=f"{section.actions[index%len(section.actions)] if section.actions else section.visual_goal} Variation {index+1} of {count}."[:1000],
                    camera_variation=self._CAMERAS[index%len(self._CAMERAS)],duration_seconds=clip,
                    recurring_character_ids=section.characters))
        shared=self._shared_usage(shots,slots,storyboard,clip)
        variants=tuple(self._variant(value_id,duration,shared,clip) for value_id,duration in sorted(audio_variant_durations.items()))
        generated_seconds=unique*clip; estimate=generated_seconds*cost_rate if cost_rate is not None else None
        over_cost=estimate is not None and configuration.maximum_generation_budget is not None and estimate>configuration.maximum_generation_budget
        over_count=configuration.maximum_scene_count is not None and unique>configuration.maximum_scene_count
        return VideoCoveragePlan(coverage_duration_seconds=coverage,provider_capabilities=capabilities,policy=configuration.policy,
            original_section_count=len(storyboard.sections),unique_scene_count=unique,derived_or_reused_scene_count=slots-unique,
            total_timeline_coverage_seconds=slots*clip,unique_shots=tuple(shots),shared_usage_plan=shared,variant_plans=variants,
            estimated_generated_seconds=generated_seconds,estimated_provider_cost=estimate,
            maximum_allowed_cost=configuration.maximum_generation_budget,confirmation_required=over_cost or over_count)

    @staticmethod
    def _section_weights(storyboard,timelines,durations):
        if timelines:
            longest=max(timelines,key=lambda key:durations.get(key,0)); timeline=timelines[longest]
            values={item.storyboard_section_id:item.end_seconds-item.start_seconds for item in timeline.segments}
            return [values.get(section.section_id,section.estimated_duration_seconds) for section in storyboard.sections]
        return [section.estimated_duration_seconds for section in storyboard.sections]

    @staticmethod
    def _allocate(total,weights):
        count=len(weights)
        if total<count:
            ranked=sorted(range(count),key=lambda i:(-weights[i],i)); values=[0]*count
            for index in ranked[:total]: values[index]=1
            return values
        values=[1]*count; remaining=total-count; denominator=sum(weights)
        raw=[remaining*w/denominator for w in weights]; floors=[math.floor(v) for v in raw]
        values=[a+b for a,b in zip(values,floors)]; left=remaining-sum(floors)
        ranked=sorted(range(count),key=lambda i:(-(raw[i]-floors[i]),i))
        for index in ranked[:left]: values[index]+=1
        return values

    @staticmethod
    def _shared_usage(shots,slots,storyboard,clip):
        structures={value.section_id:_musical_structure(value) for value in storyboard.sections}
        refrain=[shot for shot in shots if structures[shot.source_storyboard_section_id]=="refrain"]
        intro=[shot for shot in shots if structures[shot.source_storyboard_section_id]=="intro"]
        outro=[shot for shot in shots if structures[shot.source_storyboard_section_id]=="outro"]
        middle=[shot for shot in shots if shot not in intro and shot not in outro]
        ordered=[*intro,*middle]; reuse_count=slots-len(shots); pool=refrain or middle or intro or shots
        reused=[]; previous=ordered[-1].shot_id if ordered else None
        for index in range(reuse_count):
            candidates=[shot for shot in pool if shot.shot_id!=previous]
            shot=(candidates or pool)[index%len(candidates or pool)]
            reused.append(shot); previous=shot.shot_id
        sequence=[*( (shot,False) for shot in ordered),*((shot,True) for shot in reused),
            *((shot,False) for shot in outro)]
        values=[]
        for index,(shot,reused_value) in enumerate(sequence):
            values.append(CoverageUsage(order=index+1,shot_id=shot.shot_id,source_storyboard_section_id=shot.source_storyboard_section_id,
                duration_seconds=clip,reused=reused_value,reused_from_shot_id=shot.shot_id if reused_value else None))
        return tuple(values)

    @staticmethod
    def _variant(variant_id,duration,shared,clip):
        count=math.ceil(duration/clip); usages=shared[:count]; trim=count*clip-duration
        return VariantCoveragePlan(variant_id=variant_id,audio_duration_seconds=duration,usages=usages,
            final_trim_seconds=trim,coverage_seconds=count*clip,coverage_valid=len(usages)==count and 0<=trim<clip)


class VideoCoveragePlanValidator:
    """Pure validation of a persisted or in-memory coverage schedule."""
    def validate(self,plan:VideoCoveragePlan,storyboard:CreativeStoryboard,reference_sha_by_shot:dict[str,str|None]|None=None):
        errors=[]; shared=plan.shared_usage_plan
        structures={value.section_id:_musical_structure(value) for value in storyboard.sections}
        unique_ids={value.shot_id for value in plan.unique_shots}
        generated=[value.shot_id for value in shared if not value.reused]
        if len(generated)!=len(set(generated)): errors.append("provider_shot_submitted_more_than_once")
        if set(generated)!=unique_ids: errors.append("unique_shot_schedule_incomplete")
        for previous,current in zip(shared,shared[1:]):
            if current.reused and current.shot_id==previous.shot_id: errors.append("immediate_self_reuse")
        for value in shared:
            if value.reused and (value.reused_from_shot_id!=value.shot_id or
                    next(shot for shot in plan.unique_shots if shot.shot_id==value.shot_id).source_storyboard_section_id!=value.source_storyboard_section_id):
                errors.append("semantic_reuse_mismatch")
        refrain_ids={shot.shot_id for shot in plan.unique_shots if structures[shot.source_storyboard_section_id]=="refrain"}
        if refrain_ids and any(value.reused and value.shot_id not in refrain_ids for value in shared): errors.append("refrain_reuse_not_preferred")
        intro_outro={shot.shot_id for shot in plan.unique_shots if structures[shot.source_storyboard_section_id] in ("intro","outro")}
        if any(value.reused and value.shot_id in intro_outro for value in shared): errors.append("intro_or_outro_reused")
        outro_ids={shot.shot_id for shot in plan.unique_shots if structures[shot.source_storyboard_section_id]=="outro"}
        if outro_ids and (shared[-1].reused or shared[-1].shot_id not in outro_ids): errors.append("final_unique_outro_missing")
        expected=plan.coverage_duration_seconds
        if sum(value.duration_seconds for value in shared)+1e-9<expected: errors.append("longest_variant_not_covered")
        for variant in plan.variant_plans:
            if variant.usages!=shared[:len(variant.usages)]: errors.append(f"{variant.variant_id}_not_deterministic_prefix")
            if variant.coverage_seconds+1e-9<variant.audio_duration_seconds: errors.append(f"{variant.variant_id}_coverage_short")
        if reference_sha_by_shot:
            grouped={}
            for shot in plan.unique_shots:
                if not shot.recurring_character_ids: continue
                grouped.setdefault(shot.recurring_character_ids,set()).add(reference_sha_by_shot.get(shot.shot_id))
            if any(None in values or len(values)!=1 for values in grouped.values()): errors.append("recurring_reference_sha_mismatch")
        return tuple(dict.fromkeys(errors))


def _musical_structure(section):
    text=unicodedata.normalize("NFKD",f"{section.section_id} {section.section_type}".casefold())
    text="".join(value for value in text if not unicodedata.combining(value))
    groups=(("intro",("intro","introduction","opening","deschidere")),
        ("refrain",("refrain","refren","chorus")),
        ("outro",("outro","ending","conclusion","incheiere","finale")),
        ("bridge",("bridge","punte")),("verse",("verse","strofa")))
    for kind,tokens in groups:
        if any(token in text for token in tokens): return kind
    return "educational_beat"
