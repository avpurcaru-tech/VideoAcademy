import argparse
from pathlib import Path

from pydantic import ValidationError

from app.cli.episode_generate_creative import _safe_generation_error,load_brief
from app.cli.project_generate import build_services
from app.cli.song_validate import configure_utf8_output
from app.config.environment import load_application_environment
from app.config import KlingGenerationSettings
from app.creative import DeterministicEpisodeGenerator,EpisodeGenerationService,EpisodeGeneratorRegistry
from app.music import MusicPollingPolicy
from app.project import CreativeProjectGenerationService,ProjectGenerationService,ProjectRegistry
from app.services import VideoPollingPolicy
from app.production import SceneDurationPolicy
from app.series import SeriesRegistry
from app.characters import CharacterRegistry
from app.storyboard import StoryboardGenerationService,StoryboardGeneratorRegistry,StoryboardRepository


def main():
    configure_utf8_output(); load_application_environment(); parser=argparse.ArgumentParser(description="Generate a complete project from an educational creative brief.")
    parser.add_argument("--brief",required=True,type=Path); parser.add_argument("--project-id",required=True)
    parser.add_argument("--episode-generator",required=True,choices=("deterministic","openai"))
    parser.add_argument("--video-provider",default="kling"); parser.add_argument("--lyrics-provider",default="openai")
    parser.add_argument("--music-provider",default="sunoapi_org"); parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--interval",type=float,default=5); parser.add_argument("--timeout",type=float,default=900)
    parser.add_argument("--confirm",action="store_true"); args=parser.parse_args()
    try:
        if args.output.name!=args.project_id: raise ValueError("Project output directory must match project ID.")
        brief=load_brief(args.brief); registry=ProjectRegistry(args.output.parent)
        if registry.exists(args.project_id): print("Project already exists. Use project_resume to continue it."); return 1
        if not args.confirm:
            duration_policy=SceneDurationPolicy(KlingGenerationSettings.from_environment().duration)
            local_episode=EpisodeGenerationService(DeterministicEpisodeGenerator(),duration_policy)
            project=ProjectGenerationService(build_services(preflight=True),registry)
            storyboards=StoryboardGenerationService(StoryboardGeneratorRegistry().resolve("deterministic"),SeriesRegistry(),CharacterRegistry())
            CreativeProjectGenerationService(local_episode,project,registry,storyboards).preflight(brief,args.project_id,args.output,args.video_provider)
            print("Creative project preflight passed."); print("No external provider was constructed or called.")
            print("Use --confirm to authorize Episode, video, lyrics, and music generation costs."); return 2
        ProjectGenerationService.create_planned(registry,args.project_id,args.output,brief.brief_id,brief.series_id)
        series_registry=SeriesRegistry(); character_registry=CharacterRegistry()
        if brief.series_id:
            series_bible=series_registry.load(brief.series_id)
            character_registry.require_many(series_bible.resolved_character_ids)
        duration_policy=SceneDurationPolicy(KlingGenerationSettings.from_environment().duration)
        episode_service=EpisodeGenerationService(EpisodeGeneratorRegistry().resolve(args.episode_generator),duration_policy)
        project=ProjectGenerationService(build_services(args.video_provider,args.lyrics_provider,args.music_provider),registry)
        storyboards=StoryboardGenerationService(StoryboardGeneratorRegistry().resolve(args.episode_generator),series_registry,character_registry)
        print("Episode..."); print("Video..."); print("Lyrics..."); print("Music..."); print("Composition...")
        record=CreativeProjectGenerationService(episode_service,project,registry,storyboards,StoryboardRepository()).generate(brief,args.project_id,args.output,
            VideoPollingPolicy(interval_seconds=args.interval,timeout_seconds=args.timeout),
            MusicPollingPolicy(interval_seconds=args.interval,timeout_seconds=args.timeout),args.video_provider,args.music_provider)
    except ValidationError:
        print("Creative brief validation failed."); return 1
    except Exception as error:
        try:
            if 'args' in locals() and 'registry' in locals() and registry.exists(args.project_id):
                current=registry.load(args.project_id)
                if current.status.value=="planned":
                    from app.characters import CharacterRegistryError
                    from app.series import SeriesRegistryError
                    from app.project import ProjectFailureStage
                    if isinstance(error,CharacterRegistryError):
                        failure=(ProjectFailureStage.CHARACTER_RESOLUTION,"character_resolution_failed","Character resolution failed at a safe boundary.")
                    elif isinstance(error,SeriesRegistryError):
                        failure=(ProjectFailureStage.SERIES_RESOLUTION,"series_resolution_failed","Series resolution failed at a safe boundary.")
                    else:
                        failure=(ProjectFailureStage.EPISODE_GENERATION,"provider_configuration_failed","Provider configuration failed at a safe boundary.")
                    ProjectGenerationService.fail_planned(registry,args.project_id,
                        *failure)
        except Exception: pass
        diagnostic=_safe_generation_error(error)
        print(diagnostic if not diagnostic.startswith("Episode generation failed") else
              "Creative project generation failed at a safe orchestration boundary."); return 1
    print("Completed"); print("Final outputs:")
    for value in sorted(record.final_directory.glob("final-variant-*.mp4")): print(value.name)
    return 0


if __name__=="__main__": raise SystemExit(main())
