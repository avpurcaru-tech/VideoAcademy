import argparse
from pathlib import Path

from app.timeline import (
    FFmpegTimelineCompilerError,
    build_render_plan,
    compile_ffmpeg_timeline,
)

from .video_timeline_media_validate import build_validator
from .video_timeline_show import load_timeline


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile a structural FFmpeg timeline plan.")
    parser.add_argument("--timeline", required=True, type=Path)
    parser.add_argument("--show-filter-graph", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    try:
        validated = build_validator().validate(load_timeline(args.timeline))
        plan = build_render_plan(validated)
        command = compile_ffmpeg_timeline(plan, overwrite=args.overwrite)
    except FFmpegTimelineCompilerError as error:
        print(f"FFmpeg timeline compilation failed: {str(error)[:500]}")
        return 1
    except Exception:
        print("FFmpeg timeline compilation failed due to an invalid timeline, media, or local error.")
        return 1

    print(f"Inputs: {command.input_count}")
    print("Video output: yes")
    print(f"Audio output: {'yes' if command.has_audio_output else 'no'}")
    print(f"Transitions: {command.transition_count}")
    print(f"Expected duration: {plan.expected_duration_seconds}")
    print(f"Destination: {command.expected_output_path}")
    if args.show_filter_graph:
        print(f"Filter graph: {command.filter_complex}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
