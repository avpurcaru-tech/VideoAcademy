import argparse

from app.production import EpisodeProductionReconciler, EpisodeReconciliationError, ProductionRegistry

from .video_engine_task import build_video_engine


def build_reconciler() -> EpisodeProductionReconciler:
    return EpisodeProductionReconciler(build_video_engine(), ProductionRegistry())


def main() -> int:
    parser = argparse.ArgumentParser(description=("Administrative recovery: download one attached succeeded provider scene. "
                                                  "This does not submit or render the episode."))
    parser.add_argument("--production-id", required=True)
    parser.add_argument("--scene-id", required=True)
    args = parser.parse_args()
    try:
        record = build_reconciler().recover_scene(args.production_id, args.scene_id)
        scene = next(scene for scene in record.scenes if scene.scene_id == args.scene_id)
    except EpisodeReconciliationError:
        print("Episode scene recovery failed at a safe administrative boundary.")
        return 1
    except Exception:
        print("Episode scene recovery failed due to an unexpected local error.")
        return 1
    print(f"Production ID: {record.production_id}")
    print(f"Scene: {scene.scene_id}")
    print(f"Provider task ID: {scene.provider_task_id}")
    print(f"Status: {scene.normalized_status.value if scene.normalized_status else ''}")
    print(f"Local artifact: {scene.local_path or ''}")
    print("Recovery: succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
