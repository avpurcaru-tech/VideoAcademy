from datetime import datetime, timezone

from app.models import GenerationTask
from app.services import ArtifactRecord, DownloadedVideoArtifact, GenerationTaskRecord, TaskRegistry


def sync_task_record(
    task: GenerationTask,
    downloaded_artifact: DownloadedVideoArtifact | None = None,
    registry: TaskRegistry | None = None,
) -> GenerationTaskRecord:
    """Create or update one durable task manifest from provider-neutral task data."""
    task_registry = registry or TaskRegistry()
    existing = (
        task_registry.load(task.external_task_id)
        if task_registry.exists(task.external_task_id)
        else None
    )
    artifact = _artifact_record(downloaded_artifact) if downloaded_artifact else (
        existing.artifact if existing else None
    )
    record = GenerationTaskRecord(
        provider=task.provider_name,
        provider_task_id=task.external_task_id,
        external_correlation_id=task.external_correlation_id,
        normalized_status=task.normalized_status,
        created_at=(
            existing.created_at
            if existing
            else task.submitted_at or _now()
        ),
        updated_at=task.updated_at or _now(),
        artifact=artifact,
    )
    if existing:
        task_registry.update(record)
    else:
        task_registry.create(record)
    return record


def _artifact_record(artifact: DownloadedVideoArtifact) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=artifact.artifact_id,
        local_path=artifact.local_path,
        byte_size=artifact.byte_size,
        sha256=artifact.sha256,
        content_type=artifact.content_type,
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)
