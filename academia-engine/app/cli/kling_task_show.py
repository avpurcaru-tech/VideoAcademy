import argparse

from app.services import TaskRegistry, TaskRegistryCorruptedManifestError, TaskRegistryNotFoundError


def main() -> int:
    parser = argparse.ArgumentParser(description="Show one durable Kling generation-task manifest.")
    parser.add_argument("--task-id", required=True, help="The Kling provider task ID")
    args = parser.parse_args()

    try:
        record = TaskRegistry().load(args.task_id)
    except TaskRegistryNotFoundError:
        print("No local task manifest exists for this Kling task ID.")
        return 1
    except TaskRegistryCorruptedManifestError:
        print("The local task manifest does not match the registry contract.")
        return 1
    except Exception:
        print("Kling task manifest could not be loaded due to an unexpected local error.")
        return 1

    print(f"Provider: {record.provider}")
    print(f"Task ID: {record.provider_task_id}")
    print(f"External correlation ID: {record.external_correlation_id}")
    print(f"Status: {record.normalized_status.value}")
    if record.artifact:
        print(f"Artifact ID: {record.artifact.artifact_id}")
        print(f"Local path: {record.artifact.local_path}")
        print(f"Bytes: {record.artifact.byte_size}")
        print(f"SHA-256: {record.artifact.sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
