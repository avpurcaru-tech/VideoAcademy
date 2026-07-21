import argparse
from datetime import datetime,timezone

from app.cli.project_generate import _success,build_services
from app.config.environment import load_application_environment
from app.music import MusicPollingPolicy
from app.project import ProjectGenerationService,ProjectRegistry,ProjectResumeService
from app.project import ProjectFailureStage,ProjectStatus
from app.config import KlingProviderConfigurationError
from app.providers import KlingProviderRegistry
from app.services import VideoPollingPolicy


def main() -> int:
    load_application_environment(); parser=argparse.ArgumentParser(description="Resume one durable educational media project.")
    parser.add_argument("--project-id",required=True); parser.add_argument("--interval",type=float,default=5)
    parser.add_argument("--timeout",type=float,default=900); args=parser.parse_args()
    try:
        registry=ProjectRegistry()
        try: _,video_runtime=KlingProviderRegistry().construct("kling")
        except KlingProviderConfigurationError as configuration_error:
            try:
                record=registry.load(args.project_id)
                record=record.model_copy(update={"status":ProjectStatus.FAILED,
                    "failure_stage":ProjectFailureStage.VIDEO_PROVIDER_CONFIGURATION,
                    "failure_category":"video_provider_configuration_invalid",
                    "safe_message":"Kling provider configuration is invalid.","updated_at":datetime.now(timezone.utc)})
                registry.update(record)
            except Exception: pass
            print("Project resume failed at a safe orchestration boundary.")
            print("Kling configuration is invalid:")
            for field,category in configuration_error.diagnostics: print(f"- {field}: {category}")
            try: _failure(registry.load(args.project_id))
            except Exception: pass
            return 1
        generation=ProjectGenerationService(build_services(video_runtime=video_runtime),registry)
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
