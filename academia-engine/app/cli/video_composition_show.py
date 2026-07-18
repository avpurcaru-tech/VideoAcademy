import argparse
from pathlib import Path

from app.composition import VideoCompositionManifest, resolve_manifest


def load_manifest(path: Path) -> VideoCompositionManifest:
    return VideoCompositionManifest.from_json(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Show one validated video composition manifest.")
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    try:
        manifest = load_manifest(args.manifest)
        resolved = resolve_manifest(manifest)
    except Exception:
        print("Composition manifest could not be loaded or validated.")
        return 1

    scenes_by_order = sorted(manifest.scenes, key=lambda scene: scene.order)
    print(f"Composition ID: {resolved.composition_id}")
    print(f"Scenes: {resolved.source_count}")
    print(f"Destination: {resolved.destination}")
    print(f"Workspace: {resolved.workspace}")
    for index, scene in enumerate(scenes_by_order, start=1):
        print(f"{index}. {scene.scene_id} -> {scene.source_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
