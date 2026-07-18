import argparse
from pathlib import Path

from app.composition import resolve_manifest

from .video_composition_show import load_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate one video composition manifest.")
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    try:
        resolved = resolve_manifest(load_manifest(args.manifest))
    except Exception:
        print("Composition manifest is invalid.")
        return 1
    print("Composition manifest is valid.")
    print(f"Resolved scenes: {resolved.source_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
