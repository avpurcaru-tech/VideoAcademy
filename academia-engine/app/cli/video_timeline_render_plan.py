import argparse
from pathlib import Path

from app.timeline import TimelineRenderPlanError, build_render_plan

from .video_timeline_media_validate import build_validator
from .video_timeline_show import load_timeline


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a semantic timeline render plan.")
    parser.add_argument("--timeline", required=True, type=Path)
    args = parser.parse_args()
    try:
        validated = build_validator().validate(load_timeline(args.timeline))
        plan = build_render_plan(validated)
    except TimelineRenderPlanError as error:
        print(f"Timeline render plan failed: {str(error)[:500]}")
        return 1
    except Exception:
        print("Timeline render plan failed due to an invalid timeline, media, or local error.")
        return 1

    print(f"Timeline ID: {plan.timeline_id}")
    print(f"Scenes: {len(plan.scenes)}")
    print(f"Transitions: {len(plan.transitions)}")
    print(f"Expected duration: {plan.expected_duration_seconds}")
    print(f"Destination: {plan.destination}")
    print(f"Workspace: {plan.workspace}")
    for index, scene in enumerate(plan.scenes, start=1):
        print(f"{index}. {scene.scene_id}")
        print(f"   Input index: {scene.input_index}")
        print(f"   Source: {scene.source_path}")
        print(f"   Source range: {scene.source_start_seconds} -> {scene.source_end_seconds}")
        print(f"   Output range: {scene.output_start_seconds} -> {scene.output_end_seconds}")
        print(f"   Has audio: {str(scene.has_audio).lower()}")
    for transition in plan.transitions:
        print(f"{transition.from_scene_id} -> {transition.to_scene_id}")
        print(f"Kind: {transition.kind.value}")
        print(f"Duration: {transition.duration_seconds}")
        print(f"Range: {transition.start_seconds} -> {transition.end_seconds}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
