import argparse
from pathlib import Path

from app.media import FFprobeAdapter, SubprocessProcessRunner
from app.timeline import (
    FFmpegTimelineRenderer,
    TimelineRendererError,
    build_render_plan,
)

from .video_timeline_media_validate import build_validator
from .video_timeline_show import load_timeline


def build_renderer(timeout_seconds: float | None) -> FFmpegTimelineRenderer:
    runner = SubprocessProcessRunner()
    return FFmpegTimelineRenderer(
        runner,
        FFprobeAdapter(runner),
        timeout_seconds=timeout_seconds,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Render one validated semantic video timeline.")
    parser.add_argument("--timeline", required=True, type=Path)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--timeout", type=float)
    args = parser.parse_args()
    try:
        validated = build_validator().validate(load_timeline(args.timeline))
        plan = build_render_plan(validated)
        artifact = build_renderer(args.timeout).render(
            plan,
            overwrite=args.overwrite,
        )
    except TimelineRendererError as error:
        print(f"Timeline render failed: {str(error)[:500]}")
        return 1
    except Exception:
        print("Timeline render failed due to an invalid timeline, media, or local error.")
        return 1

    media = artifact.media_info
    print(f"Timeline ID: {artifact.timeline_id}")
    print(f"Saved path: {artifact.local_path}")
    print(f"Sources: {artifact.source_count}")
    print(f"Transitions: {artifact.transition_count}")
    print(f"Bytes: {artifact.byte_size}")
    print(f"SHA-256: {artifact.sha256}")
    print(f"Duration: {media.duration_seconds}")
    print(f"Resolution: {media.width}x{media.height}")
    print(f"Frame rate: {media.frame_rate}")
    print(f"Has audio: {str(media.has_audio).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
