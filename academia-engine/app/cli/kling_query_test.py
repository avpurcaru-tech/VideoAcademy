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
    parser = argparse.ArgumentParser(description="Query one Kling task by its client-supplied external ID.")
    parser.add_argument("--external-id", required=True, help="The external_id supplied when the task was created")
    args = parser.parse_args()

    try:
        task = KlingProvider().get_task_by_external_id(args.external_id)
        sync_task_record(task)
    except KlingHttpError as error:
        print_http_diagnostics(error, emit=print)
        return 1
    except KlingProviderApiError as error:
        print_provider_error(error, emit=print)
        return 1
    except KlingTaskNotFoundError:
        print(f"Task not found for external correlation ID: {args.external_id}")
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

    print(f"kling_task_id={task.external_task_id}")
    print(f"external_correlation_id={task.external_correlation_id}")
    print(f"normalized_status={task.normalized_status.value}")
    if task.error_message:
        print(f"message={task.error_message}")
    for artifact in task.artifacts:
        print(f"video_artifact_id={artifact.artifact_id}")
        print(f"video_url={artifact.url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
