import argparse
from pathlib import Path

from app.media import FFprobeAdapter, SubprocessProcessRunner
from app.production import (EpisodeProductionError, EpisodeProductionOrchestrator, EpisodeProductionRequest,
                            GenerationRequestStore, ProductionRegistry)
from app.providers import KlingProvider, KlingVideoArtifactDownloader
from app.services import TaskRegistry, VideoEngine, VideoPollingPolicy
from app.timeline import FFmpegTimelineRenderer


def build_orchestrator() -> EpisodeProductionOrchestrator:
    runner = SubprocessProcessRunner()
    probe = FFprobeAdapter(runner)
    engine = VideoEngine({"kling": KlingProvider()}, TaskRegistry(), KlingVideoArtifactDownloader())
    return EpisodeProductionOrchestrator(engine, FFmpegTimelineRenderer(runner, probe), ProductionRegistry(), probe,
                                         GenerationRequestStore())


def _policy(args) -> VideoPollingPolicy:
    return VideoPollingPolicy(interval_seconds=args.interval, timeout_seconds=args.timeout, max_attempts=args.max_attempts)


def print_result(result, *, emit=print) -> None:
    emit(f"Production ID: {result.production_id}")
    emit(f"Status: {result.status.value}")
    emit(f"Scenes: {len(result.scenes)}")
    for scene in result.scenes:
        emit(f"Scene: {scene.scene_id}")
        emit(f"Scene status: {scene.production_status.value}")
        emit(f"Provider status: {scene.normalized_status.value if scene.normalized_status else ''}")
        emit(f"Provider task ID: {scene.provider_task_id or ''}")
        emit(f"Local artifact: {scene.local_path or ''}")
    if result.final_artifact:
        artifact = result.final_artifact; media = artifact.media_info
        emit(f"Final path: {artifact.local_path}"); emit(f"Bytes: {artifact.byte_size}")
        emit(f"SHA-256: {artifact.sha256}"); emit(f"Duration: {media.duration_seconds}")
        emit(f"Resolution: {media.width}x{media.height}"); emit(f"Frame rate: {media.frame_rate}")
        emit(f"Has audio: {str(media.has_audio).lower()}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Produce a provider-neutral video episode.")
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--interval", type=float, default=2); parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--max-attempts", type=int); args = parser.parse_args()
    try:
        request = EpisodeProductionRequest.from_json(args.request.read_text(encoding="utf-8"))
        store = GenerationRequestStore()
        for reference, generation_request in zip(request.generation_request_references, request.video_requests, strict=True):
            store.create(reference, generation_request)
        result = build_orchestrator().produce(request, _policy(args))
    except EpisodeProductionError as error:
        print(f"Episode production failed: {str(error)[:500]}"); return 1
    except Exception:
        print("Episode production failed due to an invalid request or local error."); return 1
    print_result(result); return 0


if __name__ == "__main__": raise SystemExit(main())
