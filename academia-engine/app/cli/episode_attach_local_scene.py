import argparse
from pathlib import Path

from app.media import FFprobeAdapter, SubprocessProcessRunner
from app.production import EpisodeLocalArtifactError, EpisodeLocalArtifactService, ProductionRegistry


def build_service() -> EpisodeLocalArtifactService:
    runner = SubprocessProcessRunner()
    return EpisodeLocalArtifactService(ProductionRegistry(), FFprobeAdapter(runner))


def main() -> int:
    parser = argparse.ArgumentParser(description=("Administrative recovery: probe and attach one local video without "
                                                  "provider submission or fabricated provider state."))
    parser.add_argument("--production-id", required=True)
    parser.add_argument("--scene-id", required=True)
    parser.add_argument("--input", required=True, type=Path)
    args = parser.parse_args()
    try:
        record = build_service().attach_local_artifact(args.production_id, args.scene_id, args.input)
        scene = next(scene for scene in record.scenes if scene.scene_id == args.scene_id)
    except EpisodeLocalArtifactError:
        print("Local scene attachment failed at a safe administrative boundary.")
        return 1
    except Exception:
        print("Local scene attachment failed due to an unexpected local error.")
        return 1
    print(f"Production ID: {record.production_id}")
    print(f"Scene: {scene.scene_id}")
    print(f"Artifact ID: {scene.artifact_id}")
    print(f"Local artifact: {scene.local_path}")
    print(f"Bytes: {scene.byte_size}")
    print(f"SHA-256: {scene.sha256}")
    print("Attachment: succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
