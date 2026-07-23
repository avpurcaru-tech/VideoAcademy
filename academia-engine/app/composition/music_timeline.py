import hashlib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.media import (AudioVideoCompositionRequest, AudioVideoDurationPolicy,
    FFmpegAudioVideoComposer)
from app.music_timeline import MusicTimeline
from app.sync_planning import SynchronizedEditPlan
from app.timeline import (FFmpegTimelineRenderer, TimelineMediaValidator, TimelineOutput,
    TimelineScene, VideoTimeline, build_render_plan)


class StoryboardVideoClip(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    storyboard_section_id: str=Field(min_length=1,max_length=200)
    scene_id: str|None=None
    local_path: Path


class MusicTimelineCompositionRequest(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    composition_id: str=Field(min_length=1,max_length=200)
    timeline: MusicTimeline|None=None
    edit_plan: SynchronizedEditPlan|None=None
    video_clips: tuple[StoryboardVideoClip,...]=Field(min_length=1)
    shared_master_path: Path|None=None
    music_source: Path
    destination: Path
    workspace: Path
    overwrite: bool=False
    resume: bool=False

    @model_validator(mode="after")
    def unique_clip_ids(self):
        ids=[clip.scene_id or clip.storyboard_section_id for clip in self.video_clips]
        if len(ids)!=len(set(ids)): raise ValueError("Storyboard video clip identities must be unique.")
        if self.timeline is None and self.edit_plan is None and len(self.video_clips)!=1:
            raise ValueError("Legacy composition requires one preassembled video source.")
        return self


class MusicTimelineCompositionResult(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    composition_id: str
    local_path: Path
    byte_size: int=Field(gt=0)
    sha256: str=Field(pattern=r"^[a-f0-9]{64}$")
    used_music_timeline: bool
    resumed: bool=False


class MusicTimelineCompositionError(RuntimeError): pass
class MusicTimelineClipMismatchError(MusicTimelineCompositionError): pass
class MusicTimelineCompositionConflictError(MusicTimelineCompositionError): pass


class ExistingTimelineVideoRenderer:
    """Adapter over the existing validated, atomic video timeline renderer."""
    def __init__(self,probe,renderer: FFmpegTimelineRenderer): self._validator=TimelineMediaValidator(probe); self._renderer=renderer
    def render(self,timeline: VideoTimeline):
        return self._renderer.render(build_render_plan(self._validator.validate(timeline)))


class MusicTimelineComposer:
    def __init__(self,video_renderer,composer: FFmpegAudioVideoComposer):
        self._video_renderer=video_renderer; self._composer=composer

    def compose(self,request: MusicTimelineCompositionRequest) -> MusicTimelineCompositionResult:
        request=MusicTimelineCompositionRequest.model_validate(request); destination=Path(request.destination)
        if destination.is_file() and request.resume:
            return self._existing(request,destination)
        if destination.exists() and not request.overwrite:
            raise MusicTimelineCompositionConflictError("Composition destination already exists.")
        video_source=request.video_clips[0].local_path
        used_timeline=request.timeline is not None or request.edit_plan is not None
        if request.edit_plan is not None:
            clip_by_scene={clip.scene_id:clip for clip in request.video_clips if clip.scene_id}
            clip_by_section={clip.storyboard_section_id:clip for clip in request.video_clips}
            aligned=Path(request.workspace)/"timestamp-aligned-video.mp4"
            if not (request.resume and aligned.is_file()):
                decisions=request.edit_plan.decisions
                if len(decisions)==1:
                    decision=decisions[0]; middle=(decision.source_start+decision.source_end)/2
                    decisions=(decision.model_copy(update={"destination_end":decision.destination_start+(middle-decision.source_start),"source_end":middle}),
                        decision.model_copy(update={"destination_start":decision.destination_start+(middle-decision.source_start),"source_start":middle}))
                semantic=VideoTimeline(timeline_id=f"{request.composition_id}-edl",
                    scenes=tuple(TimelineScene(scene_id=f"edit-{index:04d}",
                        source_path=(clip_by_scene.get(decision.source_scene_id) or clip_by_section[decision.storyboard_section_id]).local_path,
                        order=index,trim_start_seconds=decision.source_start,trim_end_seconds=decision.source_end)
                        for index,decision in enumerate(decisions)),
                    output=TimelineOutput(destination=aligned,workspace=Path(request.workspace)/"video-render"))
                self._video_renderer.render(semantic)
            video_source=aligned
        elif request.timeline is not None:
            clip_by_id={clip.storyboard_section_id:clip for clip in request.video_clips}
            expected=tuple(segment.storyboard_section_id for segment in request.timeline.segments)
            if set(clip_by_id)!=set(expected):
                raise MusicTimelineClipMismatchError("Video clips do not match music timeline storyboard sections.")
            if request.shared_master_path is not None: video_source=request.shared_master_path
            else:
                aligned=Path(request.workspace)/"timeline-aligned-video.mp4"
                if not (request.resume and aligned.is_file()):
                    semantic=VideoTimeline(timeline_id=f"{request.composition_id}-video",
                        scenes=tuple(TimelineScene(scene_id=segment.storyboard_section_id,
                            source_path=clip_by_id[segment.storyboard_section_id].local_path,order=index,
                            trim_start_seconds=0,trim_end_seconds=segment.end_seconds-segment.start_seconds)
                            for index,segment in enumerate(request.timeline.segments)),
                        output=TimelineOutput(destination=aligned,workspace=Path(request.workspace)/"video-render"))
                    self._video_renderer.render(semantic)
                video_source=aligned
        artifact=self._composer.compose(AudioVideoCompositionRequest(video_source=video_source,
            audio_source=request.music_source,destination=destination,workspace=Path(request.workspace)/"audio-mux",
            duration_policy=(AudioVideoDurationPolicy.EXTEND_VIDEO_TO_AUDIO if request.shared_master_path is not None
                else AudioVideoDurationPolicy.TRIM_VIDEO_TO_AUDIO),overwrite=request.overwrite))
        return MusicTimelineCompositionResult(composition_id=request.composition_id,local_path=artifact.local_path,
            byte_size=artifact.byte_size,sha256=artifact.sha256,used_music_timeline=used_timeline)

    @staticmethod
    def _existing(request,destination):
        size=destination.stat().st_size
        if size<=0: raise MusicTimelineCompositionError("Existing composition is empty.")
        return MusicTimelineCompositionResult(composition_id=request.composition_id,local_path=destination,
            byte_size=size,sha256=_sha256(destination),used_music_timeline=request.timeline is not None,resumed=True)


def _sha256(path):
    digest=hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk:=stream.read(1024*1024): digest.update(chunk)
    return digest.hexdigest()
