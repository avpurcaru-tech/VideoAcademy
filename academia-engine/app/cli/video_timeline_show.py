import argparse
from pathlib import Path

from app.timeline import VideoTimeline, resolve_timeline


def load_timeline(path: Path) -> VideoTimeline:
    return VideoTimeline.from_json(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Show one validated semantic video timeline.")
    parser.add_argument("--timeline", required=True, type=Path)
    args = parser.parse_args()
    try:
        resolved = resolve_timeline(load_timeline(args.timeline))
    except Exception:
        print("Timeline could not be loaded or validated.")
        return 1
    print(f"Timeline ID: {resolved.timeline_id}")
    print(f"Scenes: {resolved.source_count}")
    print(f"Destination: {resolved.destination}")
    print(f"Workspace: {resolved.workspace}")
    for index, scene in enumerate(resolved.ordered_scenes, start=1):
        transition = scene.transition_to_next
        kind = transition.kind.value if transition else "none"
        duration = transition.duration_seconds if transition else ""
        start = scene.trim_start_seconds if scene.trim_start_seconds is not None else ""
        end = scene.trim_end_seconds if scene.trim_end_seconds is not None else ""
        print(f"{index}. {scene.scene_id}")
        print(f"   Source: {scene.source_path}")
        print(f"   Trim: {start} -> {end}")
        print(f"   Transition: {kind} ({duration})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
