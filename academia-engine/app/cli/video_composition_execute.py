import argparse
from pathlib import Path

from app.composition import CompositionExecutionError, CompositionExecutionService

from .video_assemble import build_assembly_service
from .video_composition_show import load_manifest


def build_execution_service() -> CompositionExecutionService:
    return CompositionExecutionService(build_assembly_service())


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute one video composition manifest.")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    try:
        manifest = load_manifest(args.manifest)
        result = build_execution_service().execute(manifest, overwrite=args.overwrite)
    except CompositionExecutionError as error:
        print(f"Composition execution failed: {str(error)[:500]}")
        return 1
    except Exception:
        print("Composition execution failed due to an invalid manifest or local error.")
        return 1

    media = result.media_info
    print(f"Composition ID: {result.composition_id}")
    print(f"Saved path: {result.local_path}")
    print(f"Sources: {result.source_count}")
    print(f"Bytes: {result.byte_size}")
    print(f"SHA-256: {result.sha256}")
    print(f"Duration: {media.duration_seconds}")
    print(f"Resolution: {media.width}x{media.height}")
    print(f"Frame rate: {media.frame_rate}")
    print(f"Has audio: {str(media.has_audio).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
