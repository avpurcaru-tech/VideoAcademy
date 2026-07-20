import argparse
from pathlib import Path
from app.config.environment import load_application_environment

from app.providers import KlingProvider, KlingVideoArtifactDownloader
from app.services import (
    GenerationTaskRecord,
    TaskRegistry,
    VideoEngine,
    VideoEngineError,
    VideoEngineTimeoutError,
    VideoPollingPolicy,
)


def build_video_engine() -> VideoEngine:
    """Build the production orchestration graph without coupling VideoEngine to Kling."""
    load_application_environment()
    return VideoEngine(
        providers={"kling": KlingProvider()},
        registry=TaskRegistry(),
        downloader=KlingVideoArtifactDownloader(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh or download one durable video task.")
    parser.add_argument("--provider", required=True, help="Configured video provider name")
    parser.add_argument("--task-id", required=True, help="Provider-assigned task ID")
    parser.add_argument("--refresh", action="store_true", help="Refresh normalized task state once")
    parser.add_argument("--wait", action="store_true", help="Wait for a terminal normalized status")
    parser.add_argument("--resume", action="store_true", help="Resume an existing durable workflow")
    parser.add_argument("--download", type=Path, metavar="OUTPUT_PATH", help="Download the single video artifact")
    parser.add_argument("--interval", type=float, help="Polling interval in seconds")
    parser.add_argument("--timeout", type=float, help="Polling timeout in seconds")
    parser.add_argument("--max-attempts", type=int, help="Optional maximum refresh count")
    args = parser.parse_args()

    if not (args.refresh or args.wait or args.resume or args.download):
        parser.error("one of --refresh, --wait, --resume, or --download is required")
    if args.refresh and (args.wait or args.resume or args.download):
        parser.error("--refresh cannot be combined with --wait, --resume, or --download")
    if args.resume and (args.wait or not args.download):
        parser.error("--resume requires --download and cannot be combined with --wait")
    polling = args.wait or args.resume
    if not polling and (args.interval is not None or args.timeout is not None or args.max_attempts is not None):
        parser.error("polling options require --wait or --resume")
    if polling and (args.interval is None or args.timeout is None):
        parser.error("--wait and --resume require --interval and --timeout")

    try:
        engine = build_video_engine()
        # Provider resolution remains in VideoEngine; this check makes --provider meaningful
        # without querying or constructing a provider directly in the operation path.
        if polling:
            policy = VideoPollingPolicy(
                interval_seconds=args.interval,
                timeout_seconds=args.timeout,
                max_attempts=args.max_attempts,
            )
            if args.resume:
                record = engine.resume(args.task_id, args.download, policy)
            else:
                record = (
                    engine.wait_and_download(args.task_id, args.download, policy)
                    if args.download
                    else engine.wait_until_terminal(args.task_id, policy)
                )
        elif args.download:
            record = engine.download(args.task_id, args.download)
        else:
            record = engine.refresh(args.task_id)
        if record.provider != args.provider:
            raise VideoEngineError("The registry task belongs to a different provider.")
    except VideoEngineTimeoutError:
        print("Video polling timed out.")
        return 1
    except VideoEngineError as error:
        print(f"Video engine operation failed: {error}")
        return 1
    except Exception:
        print("Video engine operation failed due to an unexpected local error.")
        return 1

    _print_record(record, emit=print)
    return 0


def _print_record(record: GenerationTaskRecord, *, emit=print) -> None:
    artifact = record.artifact
    emit(f"Provider: {record.provider}")
    emit(f"Task ID: {record.provider_task_id}")
    emit(f"External correlation ID: {record.external_correlation_id or ''}")
    emit(f"Status: {record.normalized_status.value}")
    emit(f"Artifact ID: {artifact.artifact_id if artifact else ''}")
    emit(f"Local path: {artifact.local_path if artifact else ''}")
    emit(f"Bytes: {artifact.byte_size if artifact else ''}")
    emit(f"SHA-256: {artifact.sha256 if artifact else ''}")


if __name__ == "__main__":
    raise SystemExit(main())
