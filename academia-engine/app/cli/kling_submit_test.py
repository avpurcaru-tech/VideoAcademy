import argparse
from app.config.environment import load_application_environment
from app.providers import KlingClientError, KlingHttpError, KlingProvider
from app.services import TaskRegistryError

from .kling_diagnostics import print_http_diagnostics
from .kling_task_registry import sync_task_record
from .video_request_fixture import build_smoke_test_request


def main() -> int:
    load_application_environment()
    parser = argparse.ArgumentParser(description="Submit one billable Kling test task.")
    parser.add_argument("--confirm", action="store_true", help="Confirm that this consumes Kling credits")
    args = parser.parse_args()

    if not args.confirm:
        print("Warning: this command submits one Kling task and consumes credits. Use --confirm to continue.")
        return 2

    print("Warning: a real Kling task would consume credits.")
    request = build_smoke_test_request()
    try:
        task = KlingProvider().submit_scene(request)
    except KlingHttpError as error:
        print_http_diagnostics(error, emit=print)
        return 1
    except KlingClientError:
        print("Kling message: Submission could not be completed.")
        return 1

    try:
        sync_task_record(task)
    except TaskRegistryError:
        print("Kling task was created, but its local registry manifest could not be stored.")
        return 1
    print(f"Kling task id: {task.external_task_id}")
    print(f"External correlation id: {task.external_correlation_id}")
    print(f"Provider request id: {task.provider_request_id}")
    print(f"Normalized status: {task.normalized_status.value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
