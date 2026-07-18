import argparse
from pathlib import Path

from app.timeline import resolve_timeline

from .video_timeline_show import load_timeline


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate one semantic video timeline.")
    parser.add_argument("--timeline", required=True, type=Path)
    args = parser.parse_args()
    try:
        resolved = resolve_timeline(load_timeline(args.timeline))
    except Exception:
        print("Timeline is invalid.")
        return 1
    print("Timeline is valid.")
    print(f"Resolved scenes: {resolved.source_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
