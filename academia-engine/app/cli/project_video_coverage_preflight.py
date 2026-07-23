import argparse,os,sys

from app.cli.video_probe import build_probe
from app.music_timeline import MusicTimeline
from app.project import ProjectRegistry
from app.providers import KlingProviderRegistry
from app.storyboard import CreativeStoryboard
from app.video_coverage import (VideoCoverageConfiguration,VideoCoveragePlan,VideoCoveragePlanner,VideoCoveragePlanValidator,
    VideoCoveragePolicy)


def main():
    if hasattr(sys.stdout,"reconfigure"): sys.stdout.reconfigure(encoding="utf-8",errors="replace")
    parser=argparse.ArgumentParser(description="Plan duration-driven shared video coverage without HTTP or FFmpeg.")
    parser.add_argument("--project-id",required=True); parser.add_argument("--video-provider",required=True)
    parser.add_argument("--policy",choices=tuple(value.value for value in VideoCoveragePolicy),default="balanced")
    parser.add_argument("--balanced-unique-ratio",type=float,default=.65)
    parser.add_argument("--maximum-scene-count",type=int); parser.add_argument("--maximum-generation-budget",type=float)
    args=parser.parse_args()
    try:
        project=ProjectRegistry().load(args.project_id); root=project.music_directory.parent
        storyboard=CreativeStoryboard.model_validate_json((root/"input"/"storyboard.json").read_text(encoding="utf-8"))
        probe=build_probe(); durations={}; timelines={}
        for index in (1,2):
            variant=f"variant-{index:02d}"; audio=project.music_directory/f"{variant}.mp3"
            if audio.is_file(): durations[variant]=probe.probe_audio(audio).duration_seconds
            timeline=project.music_directory/f"timeline-{variant}.json"
            if timeline.is_file(): timelines[variant]=MusicTimeline.model_validate_json(timeline.read_text(encoding="utf-8"))
        plan_path=project.video_coverage_plan_path or root/"input"/"video-coverage-plan.json"
        if plan_path.is_file(): plan=VideoCoveragePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
        else:
            capabilities=KlingProviderRegistry.capabilities(args.video_provider,os.environ)
            configuration=VideoCoverageConfiguration(policy=args.policy,balanced_unique_coverage_ratio=args.balanced_unique_ratio,
                maximum_scene_count=args.maximum_scene_count,maximum_generation_budget=args.maximum_generation_budget)
            plan=VideoCoveragePlanner().plan(durations,storyboard,capabilities,configuration,timelines)
        from app.production import SceneDurationPolicy,StoryboardVideoPlanner
        semantic=StoryboardVideoPlanner(SceneDurationPolicy(plan.provider_capabilities.selected_clip_duration)).build(
            storyboard,project.video_production_id,plan)
        reference_sha={shot.shot_id:request.scene_visual_reference.sha256 if request.scene_visual_reference else None
            for shot,request in zip(plan.unique_shots,semantic,strict=True)}
        validation_errors=VideoCoveragePlanValidator().validate(plan,storyboard,reference_sha)
    except Exception as error:
        print(f"Coverage preflight failed: {type(error).__name__}"); print("HTTP calls: 0"); print("FFmpeg calls: 0"); return 1
    print(f"Coverage duration: {plan.coverage_duration_seconds}")
    print(f"Provider clip duration: {plan.provider_capabilities.selected_clip_duration}")
    print(f"Policy: {plan.policy.value}"); print(f"Original sections: {plan.original_section_count}")
    print(f"Unique clips to generate: {plan.unique_scene_count}")
    print(f"Reused or derived clips: {plan.derived_or_reused_scene_count}")
    print(f"Total coverage: {plan.total_timeline_coverage_seconds}")
    print(f"Estimated provider cost: {plan.estimated_provider_cost if plan.estimated_provider_cost is not None else 'unconfigured'}")
    print(f"Maximum allowed cost: {plan.maximum_allowed_cost if plan.maximum_allowed_cost is not None else 'unconfigured'}")
    print("Confirmation required: "+("yes" if plan.confirmation_required else "no"))
    shots={value.shot_id:value for value in plan.unique_shots}; cursor=0.0
    print("Shared slot schedule:")
    for slot in plan.shared_usage_plan:
        shot=shots[slot.shot_id]; end=cursor+slot.duration_seconds
        print(f"Slot: {slot.order}"); print(f"Time range: {cursor:.6f}-{end:.6f}")
        print(f"Source storyboard section: {slot.source_storyboard_section_id}")
        print(f"Generated shot ID: {slot.shot_id}"); print("Usage: "+("reused" if slot.reused else "generated"))
        if slot.reused: print(f"Reused-from shot ID: {slot.reused_from_shot_id}")
        print(f"Semantic purpose: {shot.semantic_purpose}"); print(f"Action variation: {shot.action_variation}")
        print(f"Camera variation: {shot.camera_variation}"); cursor=end
    for variant in plan.variant_plans:
        print(f"Variant: {variant.variant_id}"); print(f"Duration: {variant.audio_duration_seconds}")
        print(f"Shots used: {len(variant.usages)}"); print(f"Final trim: {variant.final_trim_seconds}")
        print("Exact slot sequence: "+", ".join(f"{value.order}:{value.shot_id}" for value in variant.usages))
        print("Coverage valid: "+("yes" if variant.coverage_valid else "no"))
    print("Schedule validation: "+("valid" if not validation_errors else "invalid"))
    for error in validation_errors: print(f"Validation failure: {error}")
    print("HTTP calls: 0"); print("FFmpeg calls: 0")
    return 0 if all(value.coverage_valid for value in plan.variant_plans) and not validation_errors else 1


if __name__=="__main__": raise SystemExit(main())
