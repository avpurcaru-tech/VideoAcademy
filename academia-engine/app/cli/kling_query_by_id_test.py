import argparse

from app.config import KlingGenerationConfigurationError
from app.providers import (
    KlingHttpError,
    KlingMalformedResponseError,
    KlingProvider,
    KlingProviderApiError,
    KlingProviderContractError,
    KlingTaskNotFoundError,
)
from app.services import TaskRegistryError

from .kling_diagnostics import print_http_diagnostics, print_provider_error, print_schema_mismatch
from .kling_task_registry import sync_task_record


def main() -> int:
    parser = argparse.ArgumentParser(description="Query one Kling task by its provider task ID.")
    parser.add_argument("--task-id", required=True, help="The Kling data.id returned by Create Task")
    args = parser.parse_args()

    try:
        task = KlingProvider().get_task_by_id(args.task_id)
        sync_task_record(task)
    except KlingHttpError as error:
        print_http_diagnostics(error, emit=print)
        return 1
    except KlingProviderApiError as error:
        print_provider_error(error, emit=print)
        return 1
    except KlingTaskNotFoundError:
        print(f"Task not found for Kling task ID: {args.task_id}")
        return 1
    except KlingMalformedResponseError as error:
        print_schema_mismatch(error, emit=print)
        return 1
    except KlingProviderContractError:
        print("Kling returned an unsupported provider-contract value.")
        return 1
    except KlingGenerationConfigurationError as error:
        print(f"Invalid Kling configuration: {error}")
        return 1
    except TaskRegistryError:
        print("Kling task was queried, but its local registry manifest could not be stored.")
        return 1
    except Exception:
        print("Kling task query failed due to an unexpected local error.")
        return 1

    print(f"Kling task ID: {task.external_task_id}")
    print(f"External correlation ID: {task.external_correlation_id}")
    print(f"Normalized status: {task.normalized_status.value}")
    task_message = task.provider_metadata.get("task_message")
    if task_message:
        print(f"Task message: {task_message}")
    for artifact in task.artifacts:
        print(f"Video artifact ID: {artifact.artifact_id}")
        print(f"Video URL: {artifact.url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
