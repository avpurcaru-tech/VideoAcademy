"""Local configuration and the single web-UI composition root."""
import json,os,re,tempfile,time
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

class SecretValue:
    """A deliberately non-serializable secret requiring explicit access."""
    __slots__=("__value",)
    def __init__(self,value): self.__value=str(value)
    def reveal(self): return self.__value
    def __repr__(self): return "SecretValue(***)"
    __str__=__repr__

@dataclass(frozen=True)
class ServerSettings: host:str="127.0.0.1"; port:int=8080; open_browser:bool=True; allow_non_loopback:bool=False
@dataclass(frozen=True)
class SunoSettings: enabled:bool=False; base_url:str="https://api.sunoapi.org"; api_key:SecretValue|None=None; timeout_seconds:float=30; model:str="V4_5"; callback_url:str|None=None
@dataclass(frozen=True)
class LyricsProviderSettings: provider:str="openai"; enabled:bool=False; api_key:SecretValue|None=None; model:str|None="gpt-5-mini"
@dataclass(frozen=True)
class AssetProviderSettings: provider:str="disabled"; enabled:bool=False; api_key:SecretValue|None=None; base_url:str|None=None
@dataclass(frozen=True)
class FFmpegSettings: enabled:bool=True; executable:str="ffmpeg"; timeout_seconds:float=300

@dataclass(frozen=True,init=False)
class ApplicationSettings:
    runtime_mode:RuntimeMode; projects_root:Path; server:ServerSettings; suno:SunoSettings; lyrics:LyricsProviderSettings; assets:AssetProviderSettings; ffmpeg:FFmpegSettings
    def __init__(self,runtime_mode=RuntimeMode.DRY_RUN,projects_root=Path(".runtime/projects"),server=None,suno=None,lyrics=None,assets=None,ffmpeg=None,**legacy):
        def secret(value): return value if isinstance(value,SecretValue) else SecretValue(value) if value else None
        if server is None: server=ServerSettings()
        if lyrics is None: lyrics=LyricsProviderSettings(enabled=bool(legacy.get("openai_api_key")),api_key=secret(legacy.get("openai_api_key")),model=legacy.get("openai_lyrics_model","gpt-5-mini"))
        if suno is None: suno=SunoSettings(enabled=bool(legacy.get("suno_api_key")),api_key=secret(legacy.get("suno_api_key")),base_url=legacy.get("suno_base_url","https://api.sunoapi.org"),model=legacy.get("suno_model","V4_5"),callback_url=legacy.get("suno_callback_url"),timeout_seconds=float(legacy.get("request_timeout_seconds",30)))
        if assets is None: assets=AssetProviderSettings(provider=str(getattr(legacy.get("asset_provider_kind","disabled"),"value",legacy.get("asset_provider_kind","disabled"))))
        if ffmpeg is None: ffmpeg=FFmpegSettings(executable=legacy.get("ffmpeg_executable","ffmpeg"))
        for name,value in (("runtime_mode",RuntimeMode(runtime_mode)),("projects_root",Path(projects_root)),("server",server),("suno",suno),("lyrics",lyrics),("assets",assets),("ffmpeg",ffmpeg)): object.__setattr__(self,name,value)
        self.validate()
    @property
    def openai_api_key(self): return self.lyrics.api_key.reveal() if self.lyrics.api_key else None
    @property
    def openai_lyrics_model(self): return self.lyrics.model
    @property
    def suno_api_key(self): return self.suno.api_key.reveal() if self.suno.api_key else None
    @property
    def suno_base_url(self): return self.suno.base_url
    @property
    def suno_model(self): return self.suno.model
    @property
    def suno_callback_url(self): return self.suno.callback_url
    @property
    def request_timeout_seconds(self): return self.suno.timeout_seconds
    @property
    def asset_provider_kind(self):
        try: return VisualAssetProviderKind(self.assets.provider)
        except ValueError: return VisualAssetProviderKind.DISABLED
    @property
    def ffmpeg_executable(self): return self.ffmpeg.executable
    def validate(self):
        if self.server.host not in {"127.0.0.1","localhost","::1"} and not self.server.allow_non_loopback: raise ValueError("non-loopback host requires --allow-non-loopback")
        if not 1<=self.server.port<=65535: raise ValueError("server port must be between 1 and 65535")
        for name,value in (("Suno",self.suno.timeout_seconds),("FFmpeg",self.ffmpeg.timeout_seconds)):
            if value<=0: raise ValueError(f"{name} timeout must be positive")
        for name,url in (("Suno",self.suno.base_url),("asset",self.assets.base_url)):
            if url and not re.match(r"^https?://[^\s/]+",url): raise ValueError(f"{name} base URL is invalid")
        if self.suno.enabled and (not self.suno.api_key or not self.suno.callback_url): raise ValueError("enabled Suno provider requires API key and callback URL")
        if self.lyrics.enabled and (not self.lyrics.provider or not self.lyrics.api_key): raise ValueError("enabled lyrics provider requires provider and API key")
        if self.assets.enabled and (not self.assets.provider or not self.assets.api_key): raise ValueError("enabled asset provider requires provider and API key")
        if not str(self.projects_root): raise ValueError("projects root is required")
        return self
    @classmethod
    def load(cls,config_path=None,environ=None,cli=None):
        env=dict(os.environ if environ is None else environ); data={}
        if config_path:
            data=json.loads(Path(config_path).read_text(encoding="utf-8"))
        cli={k:v for k,v in (cli or {}).items() if v is not None}
        def choose(section,key,env_key,default=None): return cli.get(key,env.get(env_key,data.get(section,{}).get(key,default)))
        def boolean(value): return value if isinstance(value,bool) else str(value).casefold() in {"1","true","yes","on"}
        lyrics_key=choose("lyrics","api_key","ACADEMIA_LYRICS_API_KEY",env.get("OPENAI_API_KEY")); suno_key=choose("suno","api_key","ACADEMIA_SUNO_API_KEY",env.get("SUNOAPI_ORG_API_KEY")); asset_key=choose("assets","api_key","ACADEMIA_ASSET_API_KEY")
        settings=cls(runtime_mode=choose("application","runtime_mode","ACADEMIA_RUNTIME_MODE","dry_run"),projects_root=choose("application","projects_root","ACADEMIA_PROJECTS_ROOT",".runtime/projects"),
            server=ServerSettings(host=choose("server","host","ACADEMIA_SERVER_HOST","127.0.0.1"),port=int(choose("server","port","ACADEMIA_SERVER_PORT",8080)),open_browser=boolean(choose("server","open_browser","ACADEMIA_OPEN_BROWSER",True)),allow_non_loopback=boolean(cli.get("allow_non_loopback",False))),
            suno=SunoSettings(enabled=boolean(choose("suno","enabled","ACADEMIA_SUNO_ENABLED",False)),base_url=choose("suno","base_url","ACADEMIA_SUNO_BASE_URL","https://api.sunoapi.org"),api_key=SecretValue(suno_key) if suno_key else None,timeout_seconds=float(choose("suno","timeout_seconds","ACADEMIA_SUNO_TIMEOUT_SECONDS",30)),model=choose("suno","model","ACADEMIA_SUNO_MODEL","V4_5"),callback_url=choose("suno","callback_url","ACADEMIA_SUNO_CALLBACK_URL")),
            lyrics=LyricsProviderSettings(provider=choose("lyrics","provider","ACADEMIA_LYRICS_PROVIDER","openai"),enabled=boolean(choose("lyrics","enabled","ACADEMIA_LYRICS_ENABLED",False)),api_key=SecretValue(lyrics_key) if lyrics_key else None,model=choose("lyrics","model","ACADEMIA_LYRICS_MODEL","gpt-5-mini")),
            assets=AssetProviderSettings(provider=choose("assets","provider","ACADEMIA_ASSET_PROVIDER","disabled"),enabled=boolean(choose("assets","enabled","ACADEMIA_ASSET_ENABLED",False)),api_key=SecretValue(asset_key) if asset_key else None,base_url=choose("assets","base_url","ACADEMIA_ASSET_BASE_URL")),
            ffmpeg=FFmpegSettings(enabled=boolean(choose("ffmpeg","enabled","ACADEMIA_FFMPEG_ENABLED",True)),executable=choose("ffmpeg","executable","ACADEMIA_FFMPEG_EXECUTABLE","ffmpeg"),timeout_seconds=float(choose("ffmpeg","timeout_seconds","ACADEMIA_FFMPEG_TIMEOUT_SECONDS",300))))
        return settings
    @classmethod
    def from_environment(cls,environ=None): return cls.load(environ=environ)
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

class KlingAssetUiAdapter:
    """Synchronous UI adapter over Kling's submit/query/download contract."""
    def __init__(self,settings,*,provider=None,downloader=None,clock=time,poll_interval_seconds=5,generation_timeout_seconds=900):
        self.settings=settings; self._injected_provider=provider; self._injected_downloader=downloader; self.clock=clock
        self.poll_interval_seconds=float(poll_interval_seconds); self.generation_timeout_seconds=float(generation_timeout_seconds)
    def generate(self,request):
        from app.models import GenerationTaskStatus
        prompt=self._prompt(request); references=self._references(request); provider=self._injected_provider or self._provider(prompt,references)
        submitted=provider.submit_generation(SimpleNamespace(request_id=self._request_id(request.scene_id)))
        deadline=self.clock.monotonic()+self.generation_timeout_seconds
        while True:
            task=provider.get_task_by_id(submitted.external_task_id)
            if task.normalized_status==GenerationTaskStatus.FAILED: raise RuntimeError("Kling video generation failed.")
            if task.normalized_status==GenerationTaskStatus.SUCCEEDED:
                if len(task.artifacts)!=1: raise RuntimeError("Kling must return exactly one video artifact.")
                break
            remaining=deadline-self.clock.monotonic()
            if remaining<=0: raise RuntimeError("Kling video generation exceeded the 15 minute timeout.")
            self.clock.sleep(min(self.poll_interval_seconds,remaining))
        downloader=self._injected_downloader
        if downloader is None:
            from app.providers import KlingVideoArtifactDownloader
            downloader=KlingVideoArtifactDownloader(timeout_seconds=60)
        with tempfile.TemporaryDirectory() as directory:
            destination=Path(directory)/"asset.mp4"; downloaded=downloader.download_video_artifact(task.artifacts[0],destination)
            content=destination.read_bytes()
        return AssetGenerationResult(job=AssetGenerationJob(job_id=submitted.external_task_id,provider="kling",status=AssetJobStatus.COMPLETED,
            provider_metadata={"artifact_id":downloaded.artifact_id}),media_type=AssetMediaType.VIDEO,content_type="video/mp4",content=content,
            duration_seconds=task.artifacts[0].duration_seconds,provider_response={"status":task.provider_status})
    def _provider(self,prompt,references=()):
        from app.config import KlingGenerationSettings
        from app.providers.kling_client import KlingHttpClient
        from app.providers.kling_provider import KlingProvider
        settings=KlingGenerationSettings()
        mapper=None; endpoint="/text-to-video/kling-3.0"
        if references:
            from app.providers.kling_omni_video import KlingOmniUiPromptMapper
            mapper=KlingOmniUiPromptMapper(prompt,references,settings); endpoint="/omni-video/kling-3.0"
        return KlingProvider(client=KlingHttpClient(api_key=self.settings.api_key.reveal(),base_url=self.settings.base_url or "https://api-singapore.klingai.com",timeout_seconds=30),
            mapper=mapper or _KlingUiPromptMapper(prompt,settings),generation_settings=settings,endpoint=endpoint)
    @staticmethod
    def _request_id(scene_id): return (re.sub(r"[^a-z0-9_-]+","-",scene_id.casefold()).strip("-") or "scene")[:180]
    @staticmethod
    def _prompt(request):
        from app.config import KLING_PROMPT_MAX_CHARACTERS
        parts=[request.positive_prompt.strip()]
        if request.negative_prompt.strip(): parts.append(f"Avoid: {request.negative_prompt.strip()}")
        if request.feedback: parts.append(f"Revision feedback: {request.feedback.strip()}")
        value="\n\n".join(parts)
        if len(value)>KLING_PROMPT_MAX_CHARACTERS: raise ValueError(f"Kling prompt exceeds {KLING_PROMPT_MAX_CHARACTERS} characters.")
        return value
    @staticmethod
    def _references(request):
        values=request.structured_parameters.get("character_reference_urls") or {}
        if not isinstance(values,dict): raise ValueError("Character reference URLs must be a mapping.")
        selected=request.structured_parameters.get("selected_character_ids") or ()
        missing=[value for value in selected if value not in values]
        if missing: raise ValueError("Missing public Kling character reference for: "+", ".join(missing))
        return tuple((value,values[value]) for value in selected)

class _KlingUiPromptMapper:
    def __init__(self,prompt,settings): self.prompt=prompt; self.settings=settings
    def map(self,request,external_task_id,callback_url=None):
        from app.providers.kling_dtos import KlingOptions,KlingSettings,KlingTextToVideoRequest,KlingWatermarkInfo
        return KlingTextToVideoRequest(prompt=self.prompt,settings=KlingSettings(resolution=self.settings.resolution,aspect_ratio="16:9",
            duration=self.settings.duration,audio=self.settings.audio,multi_shot=self.settings.multi_shot),
            options=KlingOptions(callback_url=callback_url,external_task_id=external_task_id,watermark_info=KlingWatermarkInfo(enabled=False)))
class DryRunRenderer:
    def render(self,*args,**kwargs): raise RuntimeError("FFmpeg is disabled in this runtime mode.")
class DryRunPlanningBuilder:
    def __init__(self,stage): self.stage=stage
    def build(self,context): return PlanningBuildResult(data={"stage":self.stage,"dry_run":True,"request":context},provider_metadata={"provider":"dry_run"})

class OpenAILyricsUiAdapter:
    def __init__(self,settings): self.settings=settings
    def prompt(self,request):
        from app.providers.openai_lyrics_provider import format_lyrics_prompt
        return format_lyrics_prompt(_brief(request))
    @staticmethod
    def validate_prompt(prompt_text):
        from app.providers.openai_lyrics_provider import parse_lyrics_prompt
        parse_lyrics_prompt(prompt_text)
    def generate(self,request):
        from app.providers.openai_lyrics_provider import OpenAILyricsGenerator
        prompt=request.user_instructions or self.prompt(request)
        if request.feedback: prompt=f"{prompt}\n\nRegeneration feedback:\n{request.feedback.strip()}"
        brief=_brief(request); plan=OpenAILyricsGenerator(api_key=self.settings.openai_api_key,model=self.settings.openai_lyrics_model).generate_lyrics(brief,prompt_text=prompt)
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
        variant_id=music.approved_variant_id or music.selected_variant_id
        if not variant_id: raise ValueError("Versiunea muzicală aprobată nu are o variantă selectată.")
        variant=next((x for x in music.variants if x.variant_id==variant_id),None)
        if variant is None: raise ValueError("Varianta muzicală aprobată nu există în manifest.")
        audio=project/"music"/f"version-{music.version:03d}"/f"{variant.variant_id}.mp3"
        retrieved=suno.get_timestamped_lyrics(music.task_id,variant.audio_id)
        alignment=LyricsAlignmentNormalizer().build(variant_id=variant.variant_id,audio_artifact_id=variant.audio_id,audio_sha256=variant.sha256,
            provider_task_id=music.task_id,provider_audio_id=variant.audio_id,audio_duration_seconds=variant.duration_seconds or 1,
            language=lyrics.generation_request.language,source="suno_timestamped_lyrics",provider_words=retrieved.words,lyrics=_lyrics_plan(lyrics,context["project_id"]),instrumental=retrieved.instrumental)
        return PlanningBuildResult(data=alignment.model_dump(mode="json"),warnings=tuple(alignment.unmatched_lyrics_tokens),review_required=alignment.status.value=="review_required")
class ExistingScenePlanUiBuilder:
    def __init__(self,settings,planner=None): self.settings=settings; self.planner=planner
    def build(self,context):
        from app.lyrics_alignment import LyricsAlignment
        from app.scene_planning import ScenePlanningThresholds,SemanticScenePlanner
        project=self.settings.projects_root/context["project_id"]; alignment=LyricsAlignment.model_validate(PlanningReviewService(project).selected("alignment").data); lyrics=_approved_lyrics(project)
        planner=self.planner or SemanticScenePlanner(ScenePlanningThresholds(group_vocal_lines=True)); storyboard=SimpleNamespace(storyboard_id=context["project_id"],sections=())
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
    def __init__(self,settings,builder=None,translator=None): self.settings=settings; self.builder=builder; self.translator=translator
    def build(self,context):
        from app.prompt_generation import PromptBuilder,default_prompt_capabilities
        from app.providers.openai_visual_prompt_translator import OpenAIVisualPromptTranslator
        from app.visual_planning import VisualPlan
        project=self.settings.projects_root/context["project_id"]; builder=self.builder or PromptBuilder(); visual=VisualPlan.model_validate(PlanningReviewService(project).selected("visual_plan").data)
        bundle=builder.build_prompt_bundle(visual_plan=visual,provider="generic_video",capabilities=default_prompt_capabilities("generic_video"))
        character_positive,character_negative,character_ids=_selected_character_prompt(project)
        character_reference_urls=_selected_character_reference_urls(character_ids)
        visual_by_id={scene.visual_scene_id:scene for scene in visual.scenes}
        translator=self.translator or OpenAIVisualPromptTranslator(self.settings.openai_api_key,self.settings.openai_lyrics_model)
        translations=translator.translate({scene.visual_scene_id:scene.source_texts for scene in visual.scenes if scene.source_texts})
        return PlanningBuildResult(data={"prompts":[{"scene_id":x.scene_id,
            "positive_prompt":"; ".join(value for value in (character_positive,
                f"English visual direction: {translations[x.scene_id]}" if x.scene_id in translations else "",x.positive_prompt) if value),
            "negative_prompt":"; ".join(value for value in (x.negative_prompt,character_negative) if value),
            "structured_parameters":{**x.structured_parameters,"selected_character_ids":list(character_ids),
                "character_reference_urls":character_reference_urls,
                "source_texts":list(visual_by_id[x.scene_id].source_texts),
                "english_visual_direction":translations.get(x.scene_id)}} for x in bundle.prompts]})

def _selected_character_prompt(project):
    from app.characters import CharacterRegistry
    from .project_creation import WebProjectManifest
    manifest=WebProjectManifest.model_validate_json((Path(project)/"project.json").read_text(encoding="utf-8"))
    if manifest.characters:
        registry=CharacterRegistry(Path(project).parent.parent/"characters"); profiles=registry.require_many(manifest.selected_character_ids)
        unselected_names=tuple(value.name.casefold() for value in registry.list_profiles() if value.character_id not in manifest.selected_character_ids)
        positive=[]; negative=[]
        for profile in profiles:
            behavior=" ".join(rule for rule in profile.behavior_rules if not any(name in rule.casefold() for name in unselected_names))
            positive.append(f"Required on-screen character in every scene: {profile.name} [{profile.character_id}]. Canonical appearance: {profile.canonical_description} Behavior: {behavior}")
            negative.extend(profile.negative_rules)
        return " ".join(positive),"; ".join(negative),manifest.selected_character_ids
    character=manifest.main_character
    return f"Required on-screen character in every scene: {character.name}. Canonical appearance: {character.description}","",()

def _selected_character_reference_urls(character_ids):
    known={"luca":"https://raw.githubusercontent.com/avpurcaru-tech/VideoAcademy/main/academia-engine/assets/characters/luca-canonical.png"}
    return {character_id:known[character_id] for character_id in character_ids if character_id in known}

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
    asset=(KlingAssetUiAdapter(settings.assets) if settings.assets.enabled and settings.assets.provider=="kling" and settings.assets.api_key
        else DisabledProvider("assets","missing Kling asset configuration"))
    renderer=_production_renderer(settings)
    availability=(ProviderAvailability("lyrics",bool(settings.openai_api_key),bool(settings.openai_api_key)),
        ProviderAvailability("music",bool(settings.suno_api_key and settings.suno_callback_url),bool(settings.suno_api_key and settings.suno_callback_url)),
        ProviderAvailability("alignment",bool(settings.suno_api_key),bool(settings.suno_api_key)),ProviderAvailability("assets",bool(settings.assets.enabled and settings.assets.api_key),isinstance(asset,KlingAssetUiAdapter)),
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
    plan=_lyrics_plan(version,request.project_id); music=MusicPlan(song_id=request.project_id,tempo_bpm=request.tempo_bpm,musical_style=request.musical_style,mood=request.mood,
        instrumentation=request.instrumentation,vocal_style=request.vocal_style,target_duration_seconds=60)
    return MusicGenerationRequest(song_id=request.project_id,title=request.episode_title,lyrics=plan,music_plan=music)
def _approved_music(project):
    from .music import MusicStageService
    state,_=__import__("app.web_ui.workflow",fromlist=["WorkflowStateRepository"]).WorkflowStateRepository(project).resolve(project.name)
    return next(x for x in MusicStageService(project).versions() if x.version==state.stage("music").approved_version)
def _approved_lyrics(project):
    from .lyrics import LyricsVersion
    state,_=__import__("app.web_ui.workflow",fromlist=["WorkflowStateRepository"]).WorkflowStateRepository(project).resolve(project.name)
    return LyricsVersion.model_validate_json((project/"lyrics"/f"version-{state.stage('lyrics').approved_version:03d}.json").read_text(encoding="utf-8"))
