from datetime import datetime
from pathlib import Path
from typing import Callable

from app.models import GenerationTaskStatus
from app.services import VideoEngineError

from .contracts import EpisodeProductionStatus, ProductionRecord
from .registry import ProductionRegistry, ProductionRegistryError, ProductionRegistryNotFoundError, utc_now


class EpisodeReconciliationError(RuntimeError): pass
class EpisodeReconciliationProductionNotFoundError(EpisodeReconciliationError): pass
class EpisodeReconciliationSceneNotFoundError(EpisodeReconciliationError): pass
class EpisodeReconciliationConflictError(EpisodeReconciliationError): pass
class EpisodeReconciliationPreconditionError(EpisodeReconciliationError): pass
class EpisodeReconciliationProviderError(EpisodeReconciliationError): pass
class EpisodeReconciliationRegistryError(EpisodeReconciliationError): pass
class EpisodeSceneRecoveryError(EpisodeReconciliationError): pass


class EpisodeProductionReconciler:
    def __init__(self, video_engine, production_registry: ProductionRegistry, *, clock: Callable[[], datetime] = utc_now) -> None:
        self._engine = video_engine
        self._registry = production_registry
        self._clock = clock

    def reconcile_provider_task(self, production_id: str, scene_id: str, provider_task_id: str) -> ProductionRecord:
        record = self._load(production_id)
        index = self._scene_index(record, scene_id)
        scene = record.scenes[index]
        if not provider_task_id or not provider_task_id.strip():
            raise EpisodeReconciliationPreconditionError("Provider task ID must not be blank.")
        if scene.provider_task_id == provider_task_id:
            return record
        if scene.provider_task_id is not None:
            raise EpisodeReconciliationConflictError("Scene already has a different provider task ID.")
        if scene.local_path is not None:
            raise EpisodeReconciliationPreconditionError("Scene already has a durable local artifact.")
        try:
            task = self._engine.reconcile_existing_task(record.provider, provider_task_id)
        except VideoEngineError as error:
            raise EpisodeReconciliationProviderError("Existing provider task could not be verified.") from error
        if task.provider_task_id != provider_task_id or task.provider != record.provider:
            raise EpisodeReconciliationProviderError("Verified provider task identity does not match.")
        updated_scene = scene.model_copy(update={
            "provider_task_id": task.provider_task_id,
            "external_correlation_id": task.external_correlation_id,
            "normalized_status": task.normalized_status,
        })
        scenes = list(record.scenes); scenes[index] = updated_scene
        updated = record.model_copy(update={
            "status": EpisodeProductionStatus.PENDING,
            "scenes": tuple(scenes),
            "updated_at": self._clock(),
        })
        self._update(updated)
        return self._load(production_id)

    def recover_scene(self, production_id: str, scene_id: str) -> ProductionRecord:
        record = self._load(production_id)
        index = self._scene_index(record, scene_id)
        scene = record.scenes[index]
        if scene.local_path is not None:
            if scene.local_path.is_file():
                return record
            raise EpisodeSceneRecoveryError("Durable scene artifact is missing locally.")
        if scene.provider_task_id is None or scene.normalized_status != GenerationTaskStatus.SUCCEEDED:
            raise EpisodeSceneRecoveryError("Scene requires an attached succeeded provider task.")
        destination = record.scene_output_directory / f"scene-{scene.order + 1:04d}.mp4"
        try:
            completed = self._engine.download(scene.provider_task_id, destination)
        except VideoEngineError as error:
            raise EpisodeSceneRecoveryError("Attached scene artifact could not be recovered.") from error
        if completed.artifact is None:
            raise EpisodeSceneRecoveryError("Recovered provider task has no local artifact.")
        artifact = completed.artifact
        updated_scene = scene.model_copy(update={
            "normalized_status": completed.normalized_status,
            "local_path": artifact.local_path,
            "artifact_id": artifact.artifact_id,
            "sha256": artifact.sha256,
        })
        scenes = list(record.scenes); scenes[index] = updated_scene
        updated = record.model_copy(update={"scenes": tuple(scenes), "updated_at": self._clock()})
        self._update(updated)
        return self._load(production_id)

    def _load(self, production_id: str) -> ProductionRecord:
        try:
            return self._registry.load(production_id)
        except ProductionRegistryNotFoundError as error:
            raise EpisodeReconciliationProductionNotFoundError("Production was not found.") from error
        except ProductionRegistryError as error:
            raise EpisodeReconciliationRegistryError("Production registry could not be read safely.") from error

    @staticmethod
    def _scene_index(record: ProductionRecord, scene_id: str) -> int:
        for index, scene in enumerate(record.scenes):
            if scene.scene_id == scene_id:
                return index
        raise EpisodeReconciliationSceneNotFoundError("Production scene was not found.")

    def _update(self, record: ProductionRecord) -> None:
        try:
            self._registry.update(record)
        except ProductionRegistryError as error:
            raise EpisodeReconciliationRegistryError("Reconciled production state could not be stored.") from error
