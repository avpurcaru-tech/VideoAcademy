import argparse
from pathlib import Path

from app.config import KlingGenerationConfigurationError
from app.models import GenerationTaskStatus, VideoArtifact
from app.providers import (
    KlingHttpError,
    KlingMalformedResponseError,
    KlingProvider,
    KlingProviderApiError,
    KlingProviderContractError,
    KlingTaskNotFoundError,
    KlingVideoArtifactDownloader,
)
from app.services import (
    ArtifactDownloadError,
    VideoArtifactAmbiguityError,
    VideoArtifactNotFoundError,
    TaskRegistryError,
)

from .kling_diagnostics import print_http_diagnostics, print_provider_error, print_schema_mismatch
from .kling_task_registry import sync_task_record


def main() -> int:
    parser = argparse.ArgumentParser(description="Download one succeeded Kling video artifact.")
    parser.add_argument("--task-id", required=True, help="The Kling data.id returned by Create Task")
    parser.add_argument("--output", required=True, type=Path, help="Final local video file path")
    args = parser.parse_args()

    try:
        task = KlingProvider().get_task_by_id(args.task_id)
        if task.normalized_status != GenerationTaskStatus.SUCCEEDED:
            raise ArtifactDownloadError("Kling task is not in the succeeded state.")
        artifact = _select_single_video_artifact(task.artifacts)
        downloaded = KlingVideoArtifactDownloader().download_video_artifact(artifact, args.output)
        sync_task_record(task, downloaded)
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
    except VideoArtifactNotFoundError:
        print("No video artifact is available for this succeeded Kling task.")
        return 1
    except VideoArtifactAmbiguityError:
        print("Multiple video artifacts are available; no artifact was selected.")
        return 1
    except ArtifactDownloadError:
        print("Video artifact download could not be completed safely.")
        return 1
    except TaskRegistryError:
        print("Video was downloaded, but its local task manifest could not be stored.")
        return 1
    except Exception:
        print("Kling video download failed due to an unexpected local error.")
        return 1

    print(f"Kling task ID: {task.external_task_id}")
    print(f"Video artifact ID: {downloaded.artifact_id}")
    print(f"Saved path: {downloaded.local_path}")
    print(f"Bytes: {downloaded.byte_size}")
    print(f"SHA-256: {downloaded.sha256}")
    return 0


def _select_single_video_artifact(artifacts: list[VideoArtifact]) -> VideoArtifact:
    if not artifacts:
        raise VideoArtifactNotFoundError("No video artifact is available.")
    if len(artifacts) > 1:
        raise VideoArtifactAmbiguityError("Multiple video artifacts are available.")
    return artifacts[0]


if __name__ == "__main__":
    raise SystemExit(main())
