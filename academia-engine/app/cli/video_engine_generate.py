import argparse
from pathlib import Path

from app.services import VideoEngineError, VideoEngineTimeoutError, VideoPollingPolicy

from .video_engine_task import _print_record, build_video_engine
from .video_request_fixture import build_smoke_test_request


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one complete provider-neutral video generation workflow."
    )
    parser.add_argument("--provider", required=True, help="Configured video provider name")
    parser.add_argument("--output", required=True, type=Path, help="Explicit final video path")
    parser.add_argument("--interval", required=True, type=float, help="Polling interval in seconds")
    parser.add_argument("--timeout", required=True, type=float, help="Polling timeout in seconds")
    parser.add_argument("--max-attempts", type=int, help="Optional maximum refresh count")
    args = parser.parse_args()

    try:
        policy = VideoPollingPolicy(
            interval_seconds=args.interval,
            timeout_seconds=args.timeout,
            max_attempts=args.max_attempts,
        )
        record = build_video_engine().generate(
            build_smoke_test_request(),
            args.output,
            policy,
            provider=args.provider,
        )
    except VideoEngineTimeoutError:
        print("Video polling timed out.")
        return 1
    except VideoEngineError as error:
        print(f"Video generation workflow failed: {error}")
        return 1
    except Exception:
        print("Video generation workflow failed due to an unexpected local error.")
        return 1

    _print_record(record, emit=print)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
