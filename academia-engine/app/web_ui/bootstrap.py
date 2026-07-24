"""Single composition root for local web UI production adapters (Sprint 19.1)."""
import os,re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .assets import (AssetGenerationJob,AssetGenerationRequest,AssetGenerationResult,AssetJobStatus,
    AssetMediaType,VisualAssetProvider)
from .composition import ExistingFFmpegCompositionAdapter,FinalCompositionRenderer
from .lyrics import LyricsGenerationRequest,LyricsGenerationResult,LyricsGenerationProvider
from .music import (MusicGenerationRequest as UiMusicRequest,MusicGenerationResult as UiMusicResult,
    MusicVariantResult,SunoApiOrgMusicAdapter)
from .planning_review import PlanningBuildResult,PlanningReviewService

class RuntimeMode(str,Enum): TEST="test"; DRY_RUN="dry_run"; PRODUCTION="production"
class VisualAssetProviderKind(str,Enum): KLING="kling"; FAKE="fake"; DISABLED="disabled"
@dataclass(frozen=True)
class ApplicationSettings:
    projects_root:Path=Path(".runtime/projects"); openai_api_key:str|None=None; openai_lyrics_model:str="gpt-5-mini"
    suno_api_key:str|None=None; suno_base_url:str="https://api.sunoapi.org"; suno_model:str="V4_5"
    suno_callback_url:str|None=None; request_timeout_seconds:float=30; asset_provider_kind:VisualAssetProviderKind=VisualAssetProviderKind.DISABLED
    ffmpeg_executable:str="ffmpeg"
    @classmethod
    def from_environment(cls,environ=None):
        values=dict(os.environ if environ is None else environ)
        return cls(projects_root=Path(values.get("ACADEMIA_PROJECTS_ROOT",".runtime/projects")),openai_api_key=values.get("OPENAI_API_KEY"),
            openai_lyrics_model=values.get("OPENAI_LYRICS_MODEL","gpt-5-mini"),suno_api_key=values.get("SUNOAPI_ORG_API_KEY"),
            suno_base_url=values.get("SUNOAPI_ORG_BASE_URL","https://api.sunoapi.org"),suno_model=values.get("SUNOAPI_ORG_MODEL","V4_5"),
            suno_callback_url=values.get("SUNOAPI_ORG_CALLBACK_URL"),request_timeout_seconds=float(values.get("SUNOAPI_ORG_TIMEOUT_SECONDS","30")),
            asset_provider_kind=VisualAssetProviderKind(values.get("VISUAL_ASSET_PROVIDER","disabled")),ffmpeg_executable=values.get("FFMPEG_EXECUTABLE","ffmpeg"))
@dataclass(frozen=True)
class ProviderAvailability:
    provider_name:str; configured:bool; available:bool; reason:str|None=None
    @property
    def label(self):
        if self.available: return "Ready"
        if not self.configured: return "Missing configuration"
        return self.reason or "Unavailable"
@dataclass(frozen=True)
class ApplicationServices:
    lyrics_provider:Any; music_provider:Any; planning_builders:dict[str,Any]; asset_provider:Any
    composition_renderer:Any; availability:tuple[ProviderAvailability,...]; runtime_mode:RuntimeMode

class DisabledProvider:
    def __init__(self,name,reason): self.name=name; self.reason=reason
    def generate(self,*args,**kwargs): raise RuntimeError(f"{self.name} unavailable: {self.reason}")
class DeterministicLyricsProvider:
    def generate(self,request):
        return LyricsGenerationResult(lyrics_text=f"[Verse]\n{request.main_character_name} explores {request.episode_title}\n[Chorus]\nLearn and sing!",
            sections=("Verse","Chorus"),provider_metadata={"provider":"fake"})
class DeterministicMusicProvider:
    def generate(self,request):
        return UiMusicResult(task_id="test-task",variants=(MusicVariantResult(audio_id="test-audio-1",audio_bytes=b"test-mp3-1",duration_seconds=10),
            MusicVariantResult(audio_id="test-audio-2",audio_bytes=b"test-mp3-2",duration_seconds=10)),provider_metadata={"provider":"fake"})
class DeterministicAssetProvider:
    def generate(self,request): return AssetGenerationResult(job=AssetGenerationJob(job_id="test-asset",provider="fake",status=AssetJobStatus.COMPLETED),media_type=AssetMediaType.IMAGE,content_type="image/png",content=b"test-png")
class DryRunRenderer:
    def render(self,*args,**kwargs): raise RuntimeError("FFmpeg is disabled in this runtime mode.")
class DryRunPlanningBuilder:
    def __init__(self,stage): self.stage=stage
    def build(self,context): return PlanningBuildResult(data={"stage":self.stage,"dry_run":True,"request":context},provider_metadata={"provider":"dry_run"})

class OpenAILyricsUiAdapter:
    def __init__(self,settings): self.settings=settings
    def generate(self,request):
        from app.providers.openai_lyrics_provider import OpenAILyricsGenerator
        brief=_brief(request); plan=OpenAILyricsGenerator(api_key=self.settings.openai_api_key,model=self.settings.openai_lyrics_model).generate_lyrics(brief)
        text="\n\n".join(f"[{x.kind.value.title()}]\n"+"\n".join(line.text for line in x.lines) for x in plan.sections)
        return LyricsGenerationResult(lyrics_text=text,sections=tuple(x.kind.value for x in plan.sections),provider_metadata={"provider":"openai","model":self.settings.openai_lyrics_model})
class LazySunoMusicUiAdapter:
    def __init__(self,settings): self.settings=settings
    def generate(self,request):
        from app.providers.sunoapi_org_music_provider import RequestsSunoApiOrgTransport,SunoApiOrgMusicProvider
        provider=SunoApiOrgMusicProvider(RequestsSunoApiOrgTransport(self.settings.suno_api_key or "",base_url=self.settings.suno_base_url,
            timeout_seconds=self.settings.request_timeout_seconds),model=self.settings.suno_model,callback_url=self.settings.suno_callback_url or "")
        return SunoApiOrgMusicAdapter(provider,_music_request).generate(request)

class ExistingAlignmentUiBuilder:
    """Uses the validated timestamped-lyrics provider and normalizer."""
    def __init__(self,settings): self.settings=settings
    def build(self,context):
        from app.lyrics_alignment import LyricsAlignmentNormalizer
        project=self.settings.projects_root/context["project_id"]; music=_approved_music(project); lyrics=_approved_lyrics(project)
        provider=LazySunoMusicUiAdapter(self.settings); real=provider # construction remains lazy until here
        from app.providers.sunoapi_org_music_provider import RequestsSunoApiOrgTransport,SunoApiOrgMusicProvider
        suno=SunoApiOrgMusicProvider(RequestsSunoApiOrgTransport(self.settings.suno_api_key or "",base_url=self.settings.suno_base_url,
            timeout_seconds=self.settings.request_timeout_seconds),model=self.settings.suno_model,callback_url=self.settings.suno_callback_url or "")
        variant=next(x for x in music.variants if x.variant_id==music.approved_variant_id); audio=project/"music"/f"version-{music.version:03d}"/f"{variant.variant_id}.mp3"
        retrieved=suno.get_timestamped_lyrics(music.task_id,variant.audio_id)
        alignment=LyricsAlignmentNormalizer().build(variant_id=variant.variant_id,audio_artifact_id=variant.audio_id,audio_sha256=variant.sha256,
            provider_task_id=music.task_id,provider_audio_id=variant.audio_id,audio_duration_seconds=variant.duration_seconds or 1,
            language=lyrics.generation_request.language,source="suno_timestamped_lyrics",provider_words=retrieved.words,lyrics=_lyrics_plan(lyrics,context["project_id"]),instrumental=retrieved.instrumental)
        return PlanningBuildResult(data=alignment.model_dump(mode="json"),warnings=tuple(alignment.unmatched_lyrics_tokens),review_required=alignment.status.value=="review_required")
class ExistingScenePlanUiBuilder:
    def __init__(self,settings,planner=None): self.settings=settings; self.planner=planner
    def build(self,context):
        from app.lyrics_alignment import LyricsAlignment
        from app.scene_planning import SemanticScenePlanner
        project=self.settings.projects_root/context["project_id"]; alignment=LyricsAlignment.model_validate(PlanningReviewService(project).selected("alignment").data); lyrics=_approved_lyrics(project)
        planner=self.planner or SemanticScenePlanner(); storyboard=SimpleNamespace(storyboard_id=context["project_id"],sections=())
        value=planner.plan(context["project_id"],alignment,_lyrics_plan(lyrics,context["project_id"]),storyboard,SimpleNamespace(segments=()))
        return PlanningBuildResult(data=value.model_dump(mode="json"),warnings=tuple(x.code for x in value.warnings))
class ExistingVisualPlanUiBuilder:
    def __init__(self,settings,planner=None): self.settings=settings; self.planner=planner
    def build(self,context):
        from app.scene_planning import ScenePlan
        from app.visual_planning import ProviderNeutralVisualPlanner,default_visual_style
        project=self.settings.projects_root/context["project_id"]; planner=self.planner or ProviderNeutralVisualPlanner(); scene=ScenePlan.model_validate(PlanningReviewService(project).selected("scene_plan").data)
        value=planner.plan(scene_plan=scene,global_style=default_visual_style(planner.configuration),aspect_ratio="16:9")
        return PlanningBuildResult(data=value.model_dump(mode="json"),warnings=tuple(x.code for x in value.warnings))
class ExistingPromptUiBuilder:
    def __init__(self,settings,builder=None): self.settings=settings; self.builder=builder
    def build(self,context):
        from app.prompt_generation import PromptBuilder,default_prompt_capabilities
        from app.visual_planning import VisualPlan
        project=self.settings.projects_root/context["project_id"]; builder=self.builder or PromptBuilder(); visual=VisualPlan.model_validate(PlanningReviewService(project).selected("visual_plan").data)
        bundle=builder.build_prompt_bundle(visual_plan=visual,provider="generic_video",capabilities=default_prompt_capabilities("generic_video"))
        return PlanningBuildResult(data={"prompts":[{"scene_id":x.scene_id,"positive_prompt":x.positive_prompt,"negative_prompt":x.negative_prompt,"structured_parameters":x.structured_parameters} for x in bundle.prompts]})

def build_application_services(*,settings,runtime_mode):
    mode=RuntimeMode(runtime_mode)
    if mode in {RuntimeMode.TEST,RuntimeMode.DRY_RUN}:
        dry=mode==RuntimeMode.DRY_RUN; providers=(DeterministicLyricsProvider(),DeterministicMusicProvider(),DeterministicAssetProvider())
        builders={x:DryRunPlanningBuilder(x) for x in ("alignment","scene_plan","visual_plan","prompts","prompt_scene")}
        label="Dry-run only" if dry else None; availability=tuple(ProviderAvailability(x,True,not dry,label) for x in ("lyrics","music","assets","composition"))
        return ApplicationServices(providers[0],providers[1],builders,providers[2],DryRunRenderer(),availability,mode)
    lyrics=OpenAILyricsUiAdapter(settings) if settings.openai_api_key else DisabledProvider("lyrics","missing OpenAI configuration")
    music=LazySunoMusicUiAdapter(settings) if settings.suno_api_key and settings.suno_callback_url else DisabledProvider("music","missing Suno configuration")
    builders={"alignment":ExistingAlignmentUiBuilder(settings),"scene_plan":ExistingScenePlanUiBuilder(settings),
        "visual_plan":ExistingVisualPlanUiBuilder(settings),"prompts":ExistingPromptUiBuilder(settings)}
    asset=DisabledProvider("assets","no safe UI-to-Kling asset mapper is configured")
    renderer=_production_renderer(settings)
    availability=(ProviderAvailability("lyrics",bool(settings.openai_api_key),bool(settings.openai_api_key)),
        ProviderAvailability("music",bool(settings.suno_api_key and settings.suno_callback_url),bool(settings.suno_api_key and settings.suno_callback_url)),
        ProviderAvailability("alignment",bool(settings.suno_api_key),bool(settings.suno_api_key)),ProviderAvailability("assets",False,False,"Unavailable"),
        ProviderAvailability("composition",True,True))
    return ApplicationServices(lyrics,music,builders,asset,renderer,availability,mode)

def _production_renderer(settings):
    from app.composition import ExistingTimelineVideoRenderer,MusicTimelineComposer
    from app.media import FFmpegAudioVideoComposer,FFprobeAdapter,SubprocessProcessRunner
    from app.timeline import FFmpegTimelineRenderer
    runner=SubprocessProcessRunner(); probe=FFprobeAdapter(runner); mux=FFmpegAudioVideoComposer(runner,probe,executable=settings.ffmpeg_executable)
    composer=MusicTimelineComposer(ExistingTimelineVideoRenderer(probe,FFmpegTimelineRenderer(runner,probe)),mux)
    return ExistingFFmpegCompositionAdapter(composer,lambda request,destination:_composition_request(settings.projects_root/request.project_id,request,destination))
def _composition_request(project,request,destination):
    from app.composition import MusicTimelineCompositionRequest,StoryboardVideoClip
    from app.music_timeline import MusicTimeline,MusicTimelineSegment
    segments=tuple(MusicTimelineSegment(start_seconds=x.start_seconds,end_seconds=x.end_seconds,storyboard_section_id=x.scene_id,estimated_confidence=1) for x in request.edl)
    timeline=MusicTimeline(timeline_id=f"{request.project_id}-final",storyboard_id=request.project_id,
        music_duration_seconds=request.expected_duration_seconds,segments=segments)
    clips=tuple(StoryboardVideoClip(storyboard_section_id=x.scene_id,scene_id=x.scene_id,local_path=project/x.source_path) for x in request.edl)
    return MusicTimelineCompositionRequest(composition_id=f"{request.project_id}-final",timeline=timeline,video_clips=clips,
        music_source=project/request.music_path,destination=destination,workspace=destination.parent/"workspace",overwrite=False)
def _brief(request):
    from app.song import EducationalSongBrief
    parts=[int(x) for x in re.findall(r"\d+",request.target_age)] or [2,5]
    return EducationalSongBrief(song_id="web-song",topic=request.theme or request.episode_title,
        learning_objectives=(request.educational_goal or request.description,),language=request.language,target_age_min=parts[0],target_age_max=parts[-1],
        target_duration_seconds=60,tone="cheerful and educational",repetition_level="high")
def _lyrics_plan(version,project_id):
    from app.song import LyricsLine,LyricsPlan,LyricsSection
    sections=[]; current=[]; kind="verse"
    def emit():
        if current: sections.append(LyricsSection(section_id=f"section-{len(sections)+1:02d}",kind=kind if kind in {"intro","verse","chorus","bridge","outro"} else "verse",order=len(sections),lines=tuple(LyricsLine(line_id=f"line-{len(sections)+1:02d}-{i:02d}",text=x) for i,x in enumerate(current,1))))
    for line in version.lyrics_text.splitlines():
        if line.strip().startswith("["): emit(); current.clear(); kind=line.strip(" []").casefold()
        elif line.strip(): current.append(line.strip())
    emit()
    kinds={x.kind.value for x in sections}
    if "verse" not in kinds and sections: sections[0]=sections[0].model_copy(update={"kind":"verse"})
    if "chorus" not in {x.kind.value for x in sections}: sections.append(LyricsSection(section_id="section-chorus",kind="chorus",order=len(sections),lines=(LyricsLine(line_id="line-chorus-01",text=sections[0].lines[0].text),)))
    return LyricsPlan(song_id=project_id,title=version.generation_request.episode_title,language=version.generation_request.language,sections=tuple(sections))
def _music_request(request):
    from app.music.contracts import MusicGenerationRequest
    from app.song import MusicPlan
    lyrics=SimpleNamespace() # replaced below with validated plan
    from .lyrics import LyricsVersion
    # UI request already contains the approved durable lyrics text.
    version=SimpleNamespace(lyrics_text=request.lyrics_text,generation_request=SimpleNamespace(episode_title=request.episode_title,language=request.language))
    plan=_lyrics_plan(version,request.project_id); music=MusicPlan(song_id=request.project_id,tempo_bpm=110,musical_style="educational pop",mood="cheerful",
        instrumentation=("ukulele","xylophone"),vocal_style="clear child-friendly vocals",target_duration_seconds=60)
    return MusicGenerationRequest(song_id=request.project_id,title=request.episode_title,lyrics=plan,music_plan=music)
def _approved_music(project):
    from .music import MusicStageService
    state,_=__import__("app.web_ui.workflow",fromlist=["WorkflowStateRepository"]).WorkflowStateRepository(project).resolve(project.name)
    return next(x for x in MusicStageService(project).versions() if x.version==state.stage("music").approved_version)
def _approved_lyrics(project):
    from .lyrics import LyricsVersion
    state,_=__import__("app.web_ui.workflow",fromlist=["WorkflowStateRepository"]).WorkflowStateRepository(project).resolve(project.name)
    return LyricsVersion.model_validate_json((project/"lyrics"/f"version-{state.stage('lyrics').approved_version:03d}.json").read_text(encoding="utf-8"))
