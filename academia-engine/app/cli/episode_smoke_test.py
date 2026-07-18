import argparse
from pathlib import Path

from app.models import VideoGenerationRequest
from app.production import (
    EpisodeProductionError,
    EpisodeProductionRequest,
    EpisodeTransitionPolicy,
    GenerationRequestReference,
    GenerationRequestStore,
    ProductionRegistry,
    ProductionRegistryError,
)
from app.services import VideoPollingPolicy

from .episode_produce import build_orchestrator


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run an explicitly confirmed real two-scene episode production."
    )
    parser.add_argument("--production-id", required=True)
    parser.add_argument("--request", action="append", type=Path, dest="requests")
    parser.add_argument("--provider", default="kling")
    parser.add_argument("--scene-output-dir", type=Path)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--transition", choices=("cut", "fade"), default="cut")
    parser.add_argument("--transition-duration", type=float)
    parser.add_argument("--interval", type=float, default=2)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--max-attempts", type=int)
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--resume", action="store_true")
    return parser


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    policy = VideoPollingPolicy(
        interval_seconds=args.interval,
        timeout_seconds=args.timeout,
        max_attempts=args.max_attempts,
    )

    if args.resume:
        if args.requests:
            parser.error("--resume does not accept --request files")
        try:
            result = build_orchestrator().resume(args.production_id, policy)
        except EpisodeProductionError:
            print("Episode smoke-test resume failed at the production boundary.")
            return 1
        except Exception:
            print("Episode smoke-test resume failed due to an unexpected local error.")
            return 1
        _print_result(result)
        return 0

    if not args.confirm:
        print("Real provider generation may consume credits. Use --confirm to continue.")
        return 2
    if len(args.requests or ()) != 2:
        parser.error("new smoke production requires exactly two --request files")
    if args.scene_output_dir is None or args.workspace is None or args.output is None:
        parser.error("new smoke production requires --scene-output-dir, --workspace, and --output")

    print("Real provider generation may consume credits.")
    print(f"Production ID: {args.production_id}")
    print("Scenes to submit: 2")
    print(f"Provider: {args.provider}")

    try:
        registry = ProductionRegistry()
        if registry.exists(args.production_id):
            print("Episode production already exists; use --resume explicitly.")
            return 1
        generation_requests = tuple(
            VideoGenerationRequest.model_validate_json(path.read_text(encoding="utf-8"))
            for path in args.requests
        )
        references = tuple(
            GenerationRequestReference(
                reference_id=f"{args.production_id}-scene-{index + 1:04d}"
            )
            for index in range(2)
        )
        request_store = GenerationRequestStore()
        for reference, generation_request in zip(references, generation_requests, strict=True):
            request_store.create(reference, generation_request)
        request = EpisodeProductionRequest(
            production_id=args.production_id,
            video_requests=generation_requests,
            generation_request_references=references,
            provider=args.provider,
            scene_output_directory=args.scene_output_dir,
            final_output_path=args.output,
            media_workspace=args.workspace,
            transition_policy=EpisodeTransitionPolicy(
                kind=args.transition,
                duration_seconds=args.transition_duration,
            ),
        )
        result = build_orchestrator().produce(request, policy)
    except EpisodeProductionError:
        print("Episode smoke test failed at the production boundary.")
        return 1
    except (OSError, ValueError, ProductionRegistryError):
        print("Episode smoke test failed due to an invalid request or durable local state.")
        return 1
    except Exception:
        print("Episode smoke test failed due to an unexpected local error.")
        return 1

    _print_result(result)
    return 0


def _print_result(result, *, emit=None) -> None:
    emit = emit or print
    emit(f"Production ID: {result.production_id}")
    emit(f"Status: {result.status.value}")
    emit(f"Scenes: {len(result.scenes)}")
    for scene in result.scenes:
        emit(f"Scene: {scene.scene_id}")
        emit(f"Scene status: {scene.normalized_status.value if scene.normalized_status else ''}")
        emit(f"Provider task ID: {scene.provider_task_id or ''}")
        emit(f"Local artifact: {scene.local_path or ''}")
    if result.final_artifact is None:
        return
    artifact = result.final_artifact
    media = artifact.media_info
    emit(f"Final path: {artifact.local_path}")
    emit(f"Bytes: {artifact.byte_size}")
    emit(f"SHA-256: {artifact.sha256}")
    emit(f"Duration: {media.duration_seconds}")
    emit(f"Resolution: {media.width}x{media.height}")
    emit(f"Frame rate: {media.frame_rate}")
    emit(f"Video codec: {media.video_codec}")
    emit(f"Audio codec: {media.audio_codec or ''}")
    emit(f"Has audio: {str(media.has_audio).lower()}")


if __name__ == "__main__":
    raise SystemExit(main())
