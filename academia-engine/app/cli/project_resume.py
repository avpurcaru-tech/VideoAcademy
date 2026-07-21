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
        print("Project resume failed at a safe orchestration boundary."); return 1
    _success(record); return 0


if __name__=="__main__": raise SystemExit(main())
