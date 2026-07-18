import argparse
from pathlib import Path

from app.media import FFprobeAdapter, SubprocessProcessRunner
from app.timeline import TimelineMediaValidationError, TimelineMediaValidator

from .video_timeline_show import load_timeline


def build_validator() -> TimelineMediaValidator:
    return TimelineMediaValidator(FFprobeAdapter(SubprocessProcessRunner()))


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a timeline against local media metadata.")
    parser.add_argument("--timeline", required=True, type=Path)
    args = parser.parse_args()
    try:
        validated = build_validator().validate(load_timeline(args.timeline))
    except TimelineMediaValidationError as error:
        print(f"Timeline media validation failed: {str(error)[:500]}")
        return 1
    except Exception:
        print("Timeline media validation failed due to an invalid timeline or local error.")
        return 1

    print(f"Timeline ID: {validated.timeline_id}")
    print(f"Scenes: {validated.source_count}")
    print(f"Total duration: {validated.total_duration_seconds}")
    for index, scene in enumerate(validated.scenes, start=1):
        transition = scene.transition_to_next
        kind = transition.kind.value if transition else "none"
        duration = transition.duration_seconds if transition else ""
        print(f"{index}. {scene.scene_id}")
        print(f"   Source: {scene.source_path}")
        print(f"   Source duration: {scene.source_media_info.duration_seconds}")
        print(f"   Effective range: {scene.effective_start_seconds} -> {scene.effective_end_seconds}")
        print(f"   Effective duration: {scene.effective_duration_seconds}")
        print(f"   Transition: {kind} ({duration})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
