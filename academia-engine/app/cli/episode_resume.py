import argparse

from app.production import EpisodeProductionError
from app.services import VideoPollingPolicy
from .episode_produce import build_orchestrator, print_result


def main() -> int:
    parser = argparse.ArgumentParser(description="Resume a durable episode production.")
    parser.add_argument("--production-id", required=True); parser.add_argument("--interval", type=float, default=2)
    parser.add_argument("--timeout", type=float, default=900); parser.add_argument("--max-attempts", type=int); args = parser.parse_args()
    try:
        policy = VideoPollingPolicy(interval_seconds=args.interval, timeout_seconds=args.timeout, max_attempts=args.max_attempts)
        result = build_orchestrator().resume(args.production_id, policy)
    except EpisodeProductionError as error:
        print(f"Episode resume failed: {str(error)[:500]}"); return 1
    except Exception:
        print("Episode resume failed due to an unexpected local error."); return 1
    print_result(result); return 0


if __name__ == "__main__": raise SystemExit(main())
