"""Provider-neutral audio-synchronized shot plans and edit decision lists."""
from pathlib import Path
from pydantic import BaseModel,ConfigDict,Field,model_validator


class SynchronizedShotPlanError(RuntimeError): failure_category="synchronized_shot_plan_failed"
class VisualOnsetRequirementFailed(SynchronizedShotPlanError): failure_category="visual_onset_requirement_failed"
class EditDecisionListInvalid(SynchronizedShotPlanError): failure_category="edit_decision_list_invalid"


class VisualOnsetRequirement(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    keyword:str; first_sung_timestamp:float=Field(ge=0); visual_lead_time:float=Field(ge=0)
    minimum_visibility_duration:float=Field(gt=0); required_visual_onset:float=Field(ge=0)
    required_visual_end:float=Field(gt=0)

class SynchronizedShotUsage(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    shot_id:str; storyboard_section_id:str; alignment_line_ids:tuple[str,...]; alignment_word_ids:tuple[str,...]
    required_visual_onset:float=Field(ge=0); required_visual_end:float=Field(gt=0)
    synchronization_tolerance:float=Field(ge=0); visual_requirements:tuple[VisualOnsetRequirement,...]=()

class EditDecision(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True,allow_inf_nan=False)
    destination_start:float=Field(ge=0); destination_end:float=Field(gt=0)
    source_scene_id:str; source_start:float=Field(ge=0); source_end:float=Field(gt=0)
    storyboard_section_id:str; alignment_line_ids:tuple[str,...]=(); alignment_word_ids:tuple[str,...]=()
    transition:str="cut"
    @model_validator(mode="after")
    def no_stretch(self):
        if self.destination_end<=self.destination_start or self.source_end<=self.source_start:
            raise ValueError("Edit decision interval is invalid.")
        if abs((self.destination_end-self.destination_start)-(self.source_end-self.source_start))>.001:
            raise ValueError("Edit decisions cannot stretch footage.")
        return self

class SynchronizedEditPlan(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    variant_id:str; alignment_id:str; audio_duration_seconds:float=Field(gt=0)
    shot_usages:tuple[SynchronizedShotUsage,...]; decisions:tuple[EditDecision,...]
    final_trim_seconds:float=Field(ge=0); synchronization_valid:bool; maximum_onset_error_seconds:float=Field(ge=0)
    @model_validator(mode="after")
    def complete(self):
        if not self.decisions: raise ValueError("Edit decision list is empty.")
        if abs(self.decisions[0].destination_start)>.001: raise ValueError("Edit decision list must begin at zero.")
        for previous,current in zip(self.decisions,self.decisions[1:]):
            if abs(previous.destination_end-current.destination_start)>.001: raise ValueError("Edit decision list has a gap or overlap.")
        if abs(self.decisions[-1].destination_end-self.audio_duration_seconds)>.001: raise ValueError("Edit decision list does not cover audio.")
        return self


class AudioSynchronizedVideoPlanner:
    def __init__(self,visual_lead_time=.30,minimum_visibility_duration=1.0,synchronization_tolerance=.20):
        self.lead=visual_lead_time; self.visibility=minimum_visibility_duration; self.tolerance=synchronization_tolerance

    def plan(self,alignment,music_timeline,coverage_plan,keywords=()):
        shots_by_section={}
        for shot in coverage_plan.unique_shots: shots_by_section.setdefault(shot.source_storyboard_section_id,[]).append(shot)
        decisions=[]; usages=[]; section_offsets={key:0 for key in shots_by_section}
        for segment in music_timeline.segments:
            pool=shots_by_section.get(segment.storyboard_section_id)
            if not pool: raise SynchronizedShotPlanError("Storyboard section has no generated shot.")
            cursor=segment.start_seconds; remaining=segment.end_seconds-segment.start_seconds
            while remaining>.001:
                shot=pool[section_offsets[segment.storyboard_section_id]%len(pool)]; section_offsets[segment.storyboard_section_id]+=1
                clip_duration=coverage_plan.provider_capabilities.selected_clip_duration
                length=min(remaining,clip_duration); source_start=0.0; source_end=length
                lines=tuple(line for line in alignment.lines if line.end_seconds>cursor and line.start_seconds<cursor+length)
                line_ids=tuple(line.line_id for line in lines); word_ids=tuple(dict.fromkeys(
                    word_id for line in lines for word_id in line.word_ids))
                requirements=[]
                words={word.word_id:word for word in alignment.words}
                for word_id in word_ids:
                    word=words[word_id]
                    if word.normalized_text in {value.casefold() for value in keywords}:
                        onset=max(0,word.start_seconds-self.lead)
                        requirements.append(VisualOnsetRequirement(keyword=word.text,first_sung_timestamp=word.start_seconds,
                            visual_lead_time=self.lead,minimum_visibility_duration=self.visibility,
                            required_visual_onset=onset,required_visual_end=max(word.end_seconds,onset+self.visibility)))
                decisions.append(EditDecision(destination_start=cursor,destination_end=cursor+length,
                    source_scene_id=shot.shot_id,source_start=source_start,source_end=source_end,
                    storyboard_section_id=segment.storyboard_section_id,alignment_line_ids=line_ids,
                    alignment_word_ids=word_ids,transition="cut"))
                usages.append(SynchronizedShotUsage(shot_id=shot.shot_id,storyboard_section_id=segment.storyboard_section_id,
                    alignment_line_ids=line_ids,alignment_word_ids=word_ids,required_visual_onset=cursor,
                    required_visual_end=cursor+length,synchronization_tolerance=self.tolerance,
                    visual_requirements=tuple(requirements)))
                cursor+=length; remaining-=length
        maximum_error=max((max(0,decision.destination_start-requirement.required_visual_onset)
            for decision,usage in zip(decisions,usages) for requirement in usage.visual_requirements),default=0)
        valid=maximum_error<=self.tolerance
        if not valid: raise VisualOnsetRequirementFailed("Visual onset occurs after its allowed timestamp.")
        generated=sum(decision.source_end-decision.source_start for decision in decisions)
        return SynchronizedEditPlan(variant_id=alignment.variant_id,alignment_id=alignment.alignment_id,
            audio_duration_seconds=alignment.audio_duration_seconds,shot_usages=tuple(usages),decisions=tuple(decisions),
            final_trim_seconds=max(0,generated-alignment.audio_duration_seconds),synchronization_valid=valid,
            maximum_onset_error_seconds=maximum_error)


class SynchronizedEditPlanStore:
    def __init__(self,directory): self.directory=Path(directory)
    def path(self,variant_id): return self.directory/f"sync-plan-{variant_id}.json"
    def save(self,value):
        path=self.path(value.variant_id); path.parent.mkdir(parents=True,exist_ok=True); path.write_text(value.model_dump_json(indent=2),encoding="utf-8")
    def load(self,variant_id): return SynchronizedEditPlan.model_validate_json(self.path(variant_id).read_text(encoding="utf-8"))
