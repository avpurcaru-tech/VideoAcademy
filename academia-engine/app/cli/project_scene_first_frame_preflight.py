import argparse,json

from app.project import ProjectRegistry,ProjectNotFoundError
from app.scene_first_frames import SceneFirstFramePlan,SceneFirstFrameStore
from app.video_coverage import VideoCoveragePlan


def _fail(category):
    print(f"Failure category: {category}"); print("Kling calls: 0"); return 1


def main():
    parser=argparse.ArgumentParser(description="Read-only contextual scene first-frame planning preflight.")
    parser.add_argument("--project-id",required=True); args=parser.parse_args()
    try: project=ProjectRegistry().load(args.project_id)
    except ProjectNotFoundError: return _fail("project_not_found")
    except Exception: return _fail("project_load_failed")
    root=project.lyrics_path.parent.parent; inputs=root/"input"
    storyboard=inputs/"storyboard.json"
    if not storyboard.is_file(): return _fail("storyboard_missing")
    music=tuple(project.music_directory.glob("variant-*.mp3"))
    if not music: return _fail("music_variants_missing")
    timelines=tuple(project.music_directory.glob("timeline-variant-*.json"))
    if len(timelines)<len(music): return _fail("timelines_missing")
    coverage_path=project.video_coverage_plan_path or inputs/"video-coverage-plan.json"
    if not coverage_path.is_file(): return _fail("video_coverage_plan_missing")
    try: coverage=VideoCoveragePlan.model_validate_json(coverage_path.read_text(encoding="utf-8"))
    except Exception: return _fail("video_coverage_plan_missing")
    if not coverage.unique_shots: return _fail("unique_shot_plan_missing")
    plans_path=project.scene_first_frame_plan_path or inputs/"scene-first-frame-plans.json"
    if not plans_path.is_file(): return _fail("scene_first_frame_plan_missing")
    try:
        payload=json.loads(plans_path.read_text(encoding="utf-8"))
        plans=tuple(SceneFirstFramePlan.model_validate(value) for value in payload["plans"])
    except Exception: return _fail("scene_first_frame_plan_missing")
    by_shot={value.shot_id:value for value in plans}; frames=SceneFirstFrameStore(); valid=True
    for shot in coverage.unique_shots:
        plan=by_shot.get(shot.shot_id); category="none"
        if plan is None: category="scene_first_frame_plan_missing"
        elif not plan.background.strip(): category="scene_first_frame_background_missing"
        elif tuple(plan.recurring_character_ids)!=tuple(shot.recurring_character_ids): category="scene_first_frame_cast_mismatch"
        ready=category=="none"; frame=frames.load(plan.first_frame_id) if plan else None
        print(f"Shot ID: {shot.shot_id}")
        print(f"Storyboard section: {plan.source_storyboard_section_id if plan else shot.source_storyboard_section_id}")
        print("Character IDs: "+(", ".join(plan.recurring_character_ids) if plan else "unavailable"))
        print(f"Background description: {plan.background if plan else 'unavailable'}")
        print("Required objects: "+(", ".join(plan.required_objects) if plan and plan.required_objects else "none"))
        print(f"Character placement: {plan.character_positions if plan else 'unavailable'}")
        print(f"Camera framing: {plan.camera_framing if plan else 'unavailable'}")
        print(f"Target aspect ratio: {plan.width}:{plan.height} ({plan.aspect_ratio:.6f})" if plan else "Target aspect ratio: unavailable")
        print("Contextual frame present: "+("yes" if frame else "no"))
        print("Publication URL available: "+("yes" if frame and frame.publication_url else "no"))
        print("Ready for frame generation: "+("yes" if ready else "no"))
        print(f"Failure category: {category}")
        valid=valid and ready
    expected=project.expected_external_call_counts
    print(f"Unique contextual frames to generate: {expected.get('unique_contextual_frames',len(plans))}")
    print(f"Unique Kling clips to submit: {expected.get('unique_kling_clips',len(plans))}")
    print(f"Reused coverage slots: {expected.get('reused_coverage_slots',0)}")
    image_cost=expected.get("estimated_image_generation_cost")
    kling_cost=expected.get("estimated_kling_cost")
    print(f"Estimated image-generation cost: {image_cost if image_cost is not None else 'unavailable'}")
    print(f"Estimated Kling cost: {kling_cost if kling_cost is not None else 'unavailable'}")
    print("Confirmation required: "+("yes" if expected.get("confirmation_required",True) else "no"))
    print("Kling calls: 0")
    return 0 if valid else 1


if __name__=="__main__": raise SystemExit(main())
