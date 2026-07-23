import argparse
from pathlib import Path

from app.cli.episode_project_plan import load_episode
from app.config.environment import load_application_environment
from app.engines.director import DirectorEngine
from app.media import AudioVariantVideoComposer,FFmpegAudioVideoComposer,FFprobeAdapter,SubprocessProcessRunner
from app.composition import ExistingTimelineVideoRenderer,MusicTimelineComposer
from app.music_timeline import MusicTimelineGenerationService,MusicTimelineGeneratorRegistry
from app.timeline import FFmpegTimelineRenderer
from app.music import MusicEngine,MusicPollingPolicy,MusicProviderRegistry,MusicTaskRegistry
from app.production import EpisodeGenerationService,EpisodeProductionPlanner,GenerationRequestStore,SceneDurationPolicy
from app.config import KlingGenerationSettings
from app.project import ProjectGenerationService,ProjectRegistry,ProjectServices
from app.prompts import PromptBuilder
from app.prompts.adapters import KlingPromptAdapter
from app.providers.openai_lyrics_provider import OpenAILyricsGenerator
from app.services import VideoPollingPolicy
from app.song import EducationalSongBrief,LyricsGenerationService,MusicPlan


def _semantic_song_inputs(episode,project_id):
    duration=sum(scene.duration_seconds for scene in episode.scenes)
    brief=EducationalSongBrief(song_id=project_id,topic=episode.metadata.topic,
        learning_objectives=(episode.metadata.topic,),language=episode.metadata.language,
        target_age_min=episode.metadata.target_age_min,target_age_max=episode.metadata.target_age_max,
        target_duration_seconds=duration,tone="cheerful and educational",repetition_level="high")
    music=MusicPlan(song_id=project_id,tempo_bpm=110,musical_style="preschool educational pop",
        mood="cheerful",instrumentation=("ukulele","xylophone","light percussion"),
        vocal_style="warm clear child-friendly vocal",target_duration_seconds=duration)
    return brief,music


def build_services(video_provider="kling",lyrics_provider="openai",music_provider="sunoapi_org",preflight=False,video_runtime=None,
                   identity_validation_mode=None):
    capabilities=__import__("app.providers",fromlist=["KlingProviderRegistry"]).KlingProviderRegistry.capabilities(video_provider)
    duration_policy=SceneDurationPolicy(capabilities.selected_clip_duration)
    director=DirectorEngine(); planner=EpisodeProductionPlanner(PromptBuilder(KlingPromptAdapter()),GenerationRequestStore(),duration_policy)
    if preflight: return ProjectServices(director,planner,None,None,None,None,None,None)
    if lyrics_provider!="openai": raise RuntimeError("Lyrics provider is unsupported.")
    from app.cli.episode_produce import build_orchestrator
    if video_runtime is None:
        from app.providers import KlingProviderRegistry
        _,video_runtime=KlingProviderRegistry().construct_runtime(video_provider)
    orchestrator=build_orchestrator(video_runtime,video_provider,identity_validation_mode); episode=EpisodeGenerationService(planner,orchestrator)
    lyrics=LyricsGenerationService(OpenAILyricsGenerator())
    runtime=MusicProviderRegistry().resolve(music_provider); music_registry=MusicTaskRegistry()
    music=MusicEngine({music_provider:runtime.provider},music_registry,runtime.downloader,default_provider=music_provider)
    runner=SubprocessProcessRunner(); probe=FFprobeAdapter(runner); mux=FFmpegAudioVideoComposer(runner,probe)
    composer=AudioVariantVideoComposer(mux)
    timeline_service=MusicTimelineGenerationService(MusicTimelineGeneratorRegistry().resolve("openai"))
    timeline_composer=MusicTimelineComposer(ExistingTimelineVideoRenderer(probe,FFmpegTimelineRenderer(runner,probe)),mux)
    return ProjectServices(director,planner,episode,orchestrator,lyrics,music,composer,music_registry,
        probe,timeline_service,timeline_composer)


def main() -> int:
    load_application_environment(); parser=argparse.ArgumentParser(description="Generate one durable educational media project.")
    parser.add_argument("--episode",required=True,type=Path); parser.add_argument("--project-id",required=True)
    parser.add_argument("--video-provider",default="kling",choices=("kling","kling_image_to_video")); parser.add_argument("--lyrics-provider",default="openai")
    parser.add_argument("--music-provider",default="sunoapi_org"); parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--interval",type=float,default=5); parser.add_argument("--timeout",type=float,default=900)
    parser.add_argument("--confirm",action="store_true"); args=parser.parse_args()
    try:
        if args.output.name!=args.project_id: raise ValueError("Project output directory must match project ID.")
        episode=load_episode(args.episode); brief,music_plan=_semantic_song_inputs(episode,args.project_id)
        registry=ProjectRegistry(args.output.parent)
        service=ProjectGenerationService(build_services(args.video_provider,args.lyrics_provider,args.music_provider,
            preflight=not args.confirm),registry)
        print("Planning...")
        if not args.confirm:
            service.preflight(episode,args.project_id,args.output,args.video_provider)
            print("Preflight passed."); print("No provider task was submitted. Use --confirm to generate."); return 2
        print("Video..."); print("Lyrics..."); print("Music..."); print("Composition...")
        record=service.generate(episode,brief,music_plan,args.project_id,args.output,
            VideoPollingPolicy(interval_seconds=args.interval,timeout_seconds=args.timeout),
            MusicPollingPolicy(interval_seconds=args.interval,timeout_seconds=args.timeout),
            args.video_provider,args.music_provider)
    except Exception:
        print("Project generation failed at a safe orchestration boundary."); return 1
    _success(record); return 0


def _success(record):
    print("Completed"); print(f"Project ID: {record.project_id}"); print("Final outputs:")
    for path in sorted(record.final_directory.glob("final-variant-*.mp4")): print(path.name)


if __name__=="__main__": raise SystemExit(main())
