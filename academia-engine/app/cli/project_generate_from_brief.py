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
    parser.add_argument("--video-provider",default="kling",choices=("kling","kling_image_to_video")); parser.add_argument("--lyrics-provider",default="openai")
    parser.add_argument("--music-provider",default="sunoapi_org"); parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--interval",type=float,default=5); parser.add_argument("--timeout",type=float,default=900)
    parser.add_argument("--confirm",action="store_true")
    modes=parser.add_mutually_exclusive_group(); modes.add_argument("--plan-only",action="store_true")
    modes.add_argument("--offline-plan-only",action="store_true"); args=parser.parse_args()
    try:
        if args.output.name!=args.project_id: raise ValueError("Project output directory must match project ID.")
        brief=load_brief(args.brief); registry=ProjectRegistry(args.output.parent)
        if registry.exists(args.project_id): print("Project already exists. Use project_resume to continue it."); return 1
        if not args.confirm and not args.plan_only and not args.offline_plan_only:
            duration_policy=SceneDurationPolicy(KlingGenerationSettings.from_environment().duration)
            local_episode=EpisodeGenerationService(DeterministicEpisodeGenerator(),duration_policy)
            project=ProjectGenerationService(build_services(preflight=True),registry)
            storyboards=StoryboardGenerationService(StoryboardGeneratorRegistry().resolve("deterministic"),SeriesRegistry(),CharacterRegistry())
            CreativeProjectGenerationService(local_episode,project,registry,storyboards).preflight(brief,args.project_id,args.output,args.video_provider)
            print("Creative project preflight passed."); print("No external provider was constructed or called.")
            print("Use --confirm to authorize Episode, video, lyrics, and music generation costs."); return 2
        ProjectGenerationService.create_planned(registry,args.project_id,args.output,brief.brief_id,brief.series_id,args.video_provider)
        series_registry=SeriesRegistry(); character_registry=CharacterRegistry()
        resolved_character_ids=()
        if brief.series_id:
            series_bible=series_registry.load(brief.series_id)
            resolved_character_ids=series_bible.resolved_character_ids
            character_registry.require_many(resolved_character_ids)
        ProjectGenerationService.persist_creative_brief(registry.load(args.project_id),brief,resolved_character_ids)
        if args.offline_plan_only:
            record=_offline_plan(registry,args.project_id,args.output,args.video_provider)
            _print_plan_summary(record); return 0
        duration_policy=SceneDurationPolicy(KlingGenerationSettings.from_environment().duration)
        episode_service=EpisodeGenerationService(EpisodeGeneratorRegistry().resolve(args.episode_generator),duration_policy)
        project=ProjectGenerationService(build_services(args.video_provider,args.lyrics_provider,args.music_provider),registry,progress=print)
        storyboards=StoryboardGenerationService(StoryboardGeneratorRegistry().resolve(args.episode_generator),series_registry,character_registry)
        print("Storyboard...")
        record=CreativeProjectGenerationService(episode_service,project,registry,storyboards,StoryboardRepository()).generate(brief,args.project_id,args.output,
            VideoPollingPolicy(interval_seconds=args.interval,timeout_seconds=args.timeout),
            MusicPollingPolicy(interval_seconds=args.interval,timeout_seconds=args.timeout),args.video_provider,args.music_provider,args.plan_only)
        if args.plan_only:
            counts=dict(record.actual_external_call_counts)
            if args.episode_generator=="openai": counts["openai_calls"]=counts.get("openai_calls",0)+1
            record=project._update(record,actual_external_call_counts=counts)
            _print_plan_summary(record); return 0
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
                    from app.providers import KlingProviderCredentialsMissingError,KlingReferencePublisherUnavailableError,KlingProviderRegistryError
                    if isinstance(error,CharacterRegistryError):
                        failure=(ProjectFailureStage.CHARACTER_RESOLUTION,"character_resolution_failed","Character resolution failed at a safe boundary.")
                    elif isinstance(error,SeriesRegistryError):
                        failure=(ProjectFailureStage.SERIES_RESOLUTION,"series_resolution_failed","Series resolution failed at a safe boundary.")
                    elif isinstance(error,KlingProviderCredentialsMissingError):
                        failure=(ProjectFailureStage.VIDEO_PROVIDER_CONFIGURATION,"provider_credentials_missing","Video provider credentials are missing.")
                    elif isinstance(error,KlingReferencePublisherUnavailableError):
                        failure=(ProjectFailureStage.VIDEO_PROVIDER_CONFIGURATION,"canonical_reference_publisher_unavailable","Canonical reference publisher is unavailable.")
                    elif isinstance(error,KlingProviderRegistryError):
                        failure=(ProjectFailureStage.VIDEO_PROVIDER_CONFIGURATION,"provider_unavailable","Selected video provider is unavailable.")
                    else:
                        failure=(ProjectFailureStage.EPISODE_GENERATION,"provider_configuration_failed","Provider configuration failed at a safe boundary.")
                    ProjectGenerationService.fail_planned(registry,args.project_id,
                        *failure)
        except Exception: pass
        diagnostic=_safe_generation_error(error)
        category=getattr(error,"failure_category",None)
        if category: print(f"Failure category: {category}")
        else: print(diagnostic if not diagnostic.startswith("Episode generation failed") else
              "Creative project generation failed at a safe orchestration boundary.")
        return 1
    print("Completed"); print("Final outputs:")
    for value in sorted(record.final_directory.glob("final-variant-*.mp4")): print(value.name)
    return 0


def _offline_plan(registry,project_id,root,video_provider):
    from datetime import datetime,timezone
    from app.storyboard import CreativeStoryboard
    from app.video_coverage import VideoCoveragePlan
    from app.production import (EpisodeProductionPlanner,EpisodeTransitionPolicy,GenerationRequestStore,SceneDurationPolicy)
    from app.prompts import PromptBuilder
    from app.prompts.adapters import KlingPromptAdapter
    record=registry.load(project_id); inputs=Path(root)/"input"
    required=((inputs/"storyboard.json","storyboard_missing"),(record.lyrics_path,"lyrics_missing"),
        (inputs/"video-coverage-plan.json","video_coverage_plan_missing"))
    for path,category in required:
        if not path.is_file(): raise OfflinePlanMissingError(category)
    music=tuple(record.music_directory.glob("variant-*.mp3"))
    if not music: raise OfflinePlanMissingError("music_variants_missing")
    timelines=tuple(record.music_directory.glob("timeline-variant-*.json"))
    if len(timelines)<len(music): raise OfflinePlanMissingError("timelines_missing")
    storyboard=CreativeStoryboard.model_validate_json((inputs/"storyboard.json").read_text(encoding="utf-8"))
    coverage=VideoCoveragePlan.model_validate_json((inputs/"video-coverage-plan.json").read_text(encoding="utf-8"))
    planner=EpisodeProductionPlanner(PromptBuilder(KlingPromptAdapter()),GenerationRequestStore(),
        SceneDurationPolicy(coverage.provider_capabilities.selected_clip_duration))
    request=planner.plan(storyboard,record.video_production_id,record.video_directory/"scenes",
        record.video_directory/"workspace",record.video_directory/"master.mp4",provider=video_provider,
        transition=EpisodeTransitionPolicy(kind="cut"),coverage_plan=coverage)
    plans={}
    for item in request.video_requests:
        if item.scene_first_frame_plan: plans.setdefault(item.scene_first_frame_plan.shot_id,item.scene_first_frame_plan)
    if not plans: raise OfflinePlanMissingError("scene_first_frame_plan_missing")
    plan_path=inputs/"scene-first-frame-plans.json"
    ProjectGenerationService._persist_json(plan_path,{"plans":[value.model_dump(mode="json") for value in plans.values()]})
    reused=sum(value is not None for value in request.reuse_source_indices)
    expected={"unique_contextual_frames":len(plans),"unique_kling_clips":len(plans),"reused_coverage_slots":reused,
        "image_generation_calls":len(plans),"publication_calls":len(plans),"kling_calls":len(plans),"ffmpeg_calls":0,
        "confirmation_required":coverage.confirmation_required,"estimated_image_generation_cost":None,
        "estimated_kling_cost":coverage.estimated_provider_cost}
    updated=record.model_copy(update={"status":"awaiting_scene_first_frame_generation","video_provider":video_provider,
        "video_coverage_plan_path":inputs/"video-coverage-plan.json","scene_first_frame_plan_path":plan_path,
        "provider_capability_snapshot":coverage.provider_capabilities.model_dump(mode="json"),
        "selected_coverage_policy":coverage.policy.value,"unique_shot_ids":tuple(plans),
        "expected_external_call_counts":expected,"actual_external_call_counts":{"openai_calls":0,"suno_calls":0},
        "updated_at":datetime.now(timezone.utc)})
    registry.update(updated); return updated


class OfflinePlanMissingError(RuntimeError):
    def __init__(self,category): super().__init__(category); self.failure_category=category


def _print_plan_summary(record):
    counts=record.actual_external_call_counts; expected=record.expected_external_call_counts
    print(f"Project status: {record.status.value}")
    print(f"Unique contextual frames to generate: {expected.get('unique_contextual_frames',0)}")
    print(f"Unique Kling clips to submit: {expected.get('unique_kling_clips',0)}")
    print(f"Reused coverage slots: {expected.get('reused_coverage_slots',0)}")
    image_cost=expected.get("estimated_image_generation_cost")
    kling_cost=expected.get("estimated_kling_cost")
    print(f"Estimated image-generation cost: {image_cost if image_cost is not None else 'unavailable'}")
    print(f"Estimated Kling cost: {kling_cost if kling_cost is not None else 'unavailable'}")
    print("Confirmation required: "+("yes" if expected.get("confirmation_required",True) else "no"))
    print(f"OpenAI calls: {counts.get('openai_calls',0)}")
    print(f"Suno calls: {counts.get('suno_calls',0)}")
    print("Image-generation calls: 0"); print("Publication calls: 0")
    print("Kling calls: 0"); print("FFmpeg calls: 0")


if __name__=="__main__": raise SystemExit(main())
