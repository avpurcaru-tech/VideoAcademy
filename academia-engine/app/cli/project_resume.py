import argparse

from app.cli.project_generate import _success,build_services
from app.config.environment import load_application_environment
from app.music import MusicPollingPolicy
from app.project import ProjectGenerationService,ProjectRegistry,ProjectResumeService
from app.services import VideoPollingPolicy


def main() -> int:
    load_application_environment(); parser=argparse.ArgumentParser(description="Resume one durable educational media project.")
    parser.add_argument("--project-id",required=True); parser.add_argument("--interval",type=float,default=5)
    parser.add_argument("--timeout",type=float,default=900); args=parser.parse_args()
    try:
        registry=ProjectRegistry(); generation=ProjectGenerationService(build_services(),registry)
        print("Resuming...")
        record=ProjectResumeService(generation,registry).resume(args.project_id,
            VideoPollingPolicy(interval_seconds=args.interval,timeout_seconds=args.timeout),
            MusicPollingPolicy(interval_seconds=args.interval,timeout_seconds=args.timeout))
    except Exception:
        print("Project resume failed at a safe orchestration boundary.")
        try: _failure(ProjectRegistry().load(args.project_id))
        except Exception: pass
        return 1
    _success(record); return 0


def _failure(record):
    print(f"Project failure stage: {record.failure_stage.value if record.failure_stage else 'unavailable'}")
    print(f"Project failure category: {record.failure_category or 'unavailable'}")
    print(f"Failed scene: {record.failed_scene_id or 'unavailable'}")
    print(f"Safe message: {record.safe_message or 'No durable diagnostic is available for this legacy failure.'}")


if __name__=="__main__": raise SystemExit(main())
