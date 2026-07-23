import argparse

from app.project import ProjectRegistry
from app.production import GenerationRequestStore,ProductionRegistry
from app.scene_first_frames import SceneFirstFrameStore
from app.visual_references import LUCA_MAX_SCENE_REFERENCE,LUCA_SCENE_REFERENCE,MAX_SCENE_REFERENCE


def main():
    parser=argparse.ArgumentParser(description="Read-only contextual scene first-frame preflight.")
    parser.add_argument("--project-id",required=True); args=parser.parse_args()
    valid=True
    try:
        project=ProjectRegistry().load(args.project_id)
        production=ProductionRegistry().load(project.video_production_id)
        requests=GenerationRequestStore(); frames=SceneFirstFrameStore()
    except Exception as error:
        print(f"Failure category: {getattr(error,'failure_category','scene_first_frame_plan_failed')}")
        print("Kling calls: 0"); return 1
    canonical={LUCA_MAX_SCENE_REFERENCE.sha256,LUCA_SCENE_REFERENCE.sha256,MAX_SCENE_REFERENCE.sha256}
    for scene in production.scenes:
        if scene.derived_from_scene_id is not None: continue
        request=requests.resolve(scene.generation_request_reference); plan=request.scene_first_frame_plan
        frame=frames.load(plan.first_frame_id) if plan else None
        ratio_ok=bool(frame and plan and abs(frame.width/frame.height-plan.width/plan.height)<=.01)
        generic=bool(frame and frame.sha256 in canonical)
        published=bool(frame and frame.publication_url)
        print(f"Shot: {scene.scene_id}")
        print(f"Storyboard section: {plan.source_storyboard_section_id if plan else 'unavailable'}")
        print("Characters: "+(", ".join(plan.recurring_character_ids) if plan else "unavailable"))
        print("Background specified: "+("yes" if plan and plan.background.strip() else "no"))
        print("Required objects: "+(", ".join(plan.required_objects) if plan else "unavailable"))
        print(f"Camera framing: {plan.camera_framing if plan else 'unavailable'}")
        print("Contextual frame present: "+("yes" if frame else "no"))
        print("Generic identity sheet used directly: "+("yes" if generic else "no"))
        print("Aspect ratio valid: "+("yes" if ratio_ok else "no"))
        print("Publication URL available: "+("yes" if published else "no"))
        valid=valid and bool(plan and frame and not generic and ratio_ok and published)
    print("Kling calls: 0")
    return 0 if valid else 1


if __name__=="__main__": raise SystemExit(main())
