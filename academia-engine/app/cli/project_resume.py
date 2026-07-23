import argparse
from datetime import datetime,timezone

from app.cli.project_generate import _success,build_services
from app.config.environment import load_application_environment
from app.music import MusicPollingPolicy
from app.project import ProjectGenerationService,ProjectRegistry,ProjectResumeService
from app.project import ProjectFailureStage,ProjectStatus
from app.config import KlingProviderConfigurationError
from app.providers import (KlingProviderRegistry,KlingProviderRegistryError,KlingProviderCredentialsMissingError,
    KlingReferencePublisherUnavailableError)
from app.services import VideoPollingPolicy


def main() -> int:
    load_application_environment(); parser=argparse.ArgumentParser(description="Resume one durable educational media project.")
    parser.add_argument("--project-id",required=True); parser.add_argument("--interval",type=float,default=5)
    parser.add_argument("--timeout",type=float,default=900); args=parser.parse_args()
    try:
        registry=ProjectRegistry()
        existing=registry.load(args.project_id)
        selected_provider=existing.video_provider
        if selected_provider is None and existing.video_coverage_plan_path and existing.video_coverage_plan_path.is_file():
            from app.video_coverage import VideoCoveragePlan
            coverage=VideoCoveragePlan.model_validate_json(existing.video_coverage_plan_path.read_text(encoding="utf-8"))
            selected_provider=coverage.provider_capabilities.provider_name
            existing=existing.model_copy(update={"video_provider":selected_provider,"updated_at":datetime.now(timezone.utc)})
            registry.update(existing)
        selected_provider=selected_provider or "kling"
        if existing.failure_stage==ProjectFailureStage.STORYBOARD_GENERATION and not (existing.lyrics_path.parent.parent/"input"/"storyboard.json").is_file():
            try:
                from app.cli.project_retry_storyboard import retry_storyboard
                retry_storyboard(args.project_id,registry)
            except Exception:
                print("Project resume failed during storyboard recovery at a safe boundary.")
                try: _failure(registry.load(args.project_id))
                except Exception: pass
                return 1
        try: _,video_runtime=KlingProviderRegistry().construct_runtime(selected_provider)
        except (KlingProviderConfigurationError,KlingProviderRegistryError) as configuration_error:
            if isinstance(configuration_error,KlingProviderCredentialsMissingError): category="provider_credentials_missing"; message="Video provider credentials are missing."
            elif isinstance(configuration_error,KlingReferencePublisherUnavailableError): category="canonical_reference_publisher_unavailable"; message="Canonical reference publisher is unavailable."
            elif isinstance(configuration_error,KlingProviderRegistryError): category="provider_unavailable"; message="Selected video provider is unavailable."
            else: category="video_provider_configuration_invalid"; message="Kling provider configuration is invalid."
            try:
                record=registry.load(args.project_id)
                record=record.model_copy(update={"status":ProjectStatus.FAILED,
                    "failure_stage":ProjectFailureStage.VIDEO_PROVIDER_CONFIGURATION,
                    "failure_category":category,"safe_message":message,"updated_at":datetime.now(timezone.utc)})
                registry.update(record)
            except Exception: pass
            print("Project resume failed at a safe orchestration boundary.")
            if getattr(configuration_error,"diagnostics",None):
                print("Kling configuration is invalid:")
                for field,value in configuration_error.diagnostics: print(f"- {field}: {value}")
            try: _failure(registry.load(args.project_id))
            except Exception: pass
            return 1
        generation=ProjectGenerationService(build_services(selected_provider,video_runtime=video_runtime,
            identity_validation_mode=existing.identity_validation_mode),registry)
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
    if record.failure_stage==ProjectFailureStage.COMPOSITION:
        print(f"Failed variant: {record.failed_variant_id or 'unavailable'}")
    else: print(f"Failed scene: {record.failed_scene_id or 'unavailable'}")
    print(f"Safe message: {record.safe_message or 'No durable diagnostic is available for this legacy failure.'}")
    if record.failure_details:
        print("Storyboard validation failed:")
        for detail in record.failure_details: print(f"- {detail}")
    if record.provider_http_status is not None: print(f"HTTP status: {record.provider_http_status}")
    if record.provider_request_id: print(f"Request ID: {record.provider_request_id}")
    if record.provider_model: print(f"Model: {record.provider_model}")
    if record.provider_retry_after: print(f"Retry-After: {record.provider_retry_after}")
    if record.submit_http_status is not None: print(f"HTTP status: {record.submit_http_status}")
    if record.submit_provider_code is not None: print(f"Kling code: {record.submit_provider_code}")
    if record.submit_provider_message: print(f"Kling message: {record.submit_provider_message}")
    if record.submit_request_id: print(f"Request ID: {record.submit_request_id}")
    if record.submit_provider_task_id: print(f"Provider task ID: {record.submit_provider_task_id}")
    if record.submit_response_shape:
        print("Response shape:")
        for entry in record.submit_response_shape: print(f"- {entry}")
    if record.query_http_status is not None: print(f"HTTP status: {record.query_http_status}")
    if record.query_provider_code is not None: print(f"Kling code: {record.query_provider_code}")
    if record.query_provider_task_id: print(f"Provider task ID: {record.query_provider_task_id}")
    if record.query_response_shape:
        print("Response shape:")
        for entry in record.query_response_shape: print(f"- {entry}")


if __name__=="__main__": raise SystemExit(main())
