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
        duration_policy=SceneDurationPolicy(KlingGenerationSettings.from_environment().duration)
        if not args.confirm:
            local_episode=EpisodeGenerationService(DeterministicEpisodeGenerator(),duration_policy)
            project=ProjectGenerationService(build_services(preflight=True),registry)
            CreativeProjectGenerationService(local_episode,project,registry).preflight(brief,args.project_id,args.output,args.video_provider)
            print("Creative project preflight passed."); print("No external provider was constructed or called.")
            print("Use --confirm to authorize Episode, video, lyrics, and music generation costs."); return 2
        episode_service=EpisodeGenerationService(EpisodeGeneratorRegistry().resolve(args.episode_generator),duration_policy)
        project=ProjectGenerationService(build_services(args.video_provider,args.lyrics_provider,args.music_provider),registry)
        print("Episode..."); print("Video..."); print("Lyrics..."); print("Music..."); print("Composition...")
        record=CreativeProjectGenerationService(episode_service,project,registry).generate(brief,args.project_id,args.output,
            VideoPollingPolicy(interval_seconds=args.interval,timeout_seconds=args.timeout),
            MusicPollingPolicy(interval_seconds=args.interval,timeout_seconds=args.timeout),args.video_provider,args.music_provider)
    except ValidationError:
        print("Creative brief validation failed."); return 1
    except Exception as error:
        diagnostic=_safe_generation_error(error)
        print(diagnostic if not diagnostic.startswith("Episode generation failed") else
              "Creative project generation failed at a safe orchestration boundary."); return 1
    print("Completed"); print("Final outputs:")
    for value in sorted(record.final_directory.glob("final-variant-*.mp4")): print(value.name)
    return 0


if __name__=="__main__": raise SystemExit(main())
