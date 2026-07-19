import hashlib
import os
from datetime import datetime
from pathlib import Path
from typing import Callable

from app.composition.paths import normalized_local_path, validate_local_path

from .contracts import EpisodeProductionStatus, EpisodeSceneStatus, ProductionRecord
from .registry import ProductionRegistry, ProductionRegistryError, ProductionRegistryNotFoundError, utc_now


class EpisodeLocalArtifactError(RuntimeError): pass
class EpisodeLocalArtifactProductionNotFoundError(EpisodeLocalArtifactError): pass
class EpisodeLocalArtifactSceneNotFoundError(EpisodeLocalArtifactError): pass
class EpisodeLocalArtifactSourceError(EpisodeLocalArtifactError): pass
class EpisodeLocalArtifactMediaError(EpisodeLocalArtifactError): pass
class EpisodeLocalArtifactConflictError(EpisodeLocalArtifactError): pass
class EpisodeLocalArtifactRegistryError(EpisodeLocalArtifactError): pass


class EpisodeLocalArtifactService:
    def __init__(self, production_registry: ProductionRegistry, probe, *, clock: Callable[[], datetime] = utc_now) -> None:
        self._registry = production_registry
        self._probe = probe
        self._clock = clock

    def attach_local_artifact(self, production_id: str, scene_id: str, source_path: Path) -> ProductionRecord:
        record = self._load(production_id)
        index = self._scene_index(record, scene_id)
        scene = record.scenes[index]
        if scene.provider_task_id is not None:
            raise EpisodeLocalArtifactConflictError("Provider-backed scenes cannot be replaced manually.")
        try:
            validate_local_path(source_path, "Local scene artifact")
        except ValueError as error:
            raise EpisodeLocalArtifactSourceError("Local scene artifact path is invalid.") from error
        source = Path(source_path)
        if not source.exists():
            raise EpisodeLocalArtifactSourceError("Local scene artifact is missing.")
        if not source.is_file():
            raise EpisodeLocalArtifactSourceError("Local scene artifact is not a regular file.")

        destination = record.scene_output_directory / f"{scene.scene_id}.mp4"
        same_file = normalized_local_path(source) == normalized_local_path(destination)
        if scene.local_path is not None:
            if normalized_local_path(scene.local_path) == normalized_local_path(destination) and destination.is_file():
                return record
            raise EpisodeLocalArtifactConflictError("Scene already has a different durable artifact.")
        if destination.exists() and not same_file:
            raise EpisodeLocalArtifactConflictError("Deterministic scene destination already exists.")

        published = False
        temporary = destination.with_suffix(".mp4.part")
        try:
            if same_file:
                byte_size, sha256 = self._hash_file(source)
                self._probe_video(source)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                byte_size, sha256 = self._copy_and_hash(source, temporary)
                self._probe_video(temporary)
                os.replace(temporary, destination)
                published = True
            updated_scene = scene.model_copy(update={
                "local_path": destination,
                "artifact_id": f"local:{sha256}",
                "byte_size": byte_size,
                "sha256": sha256,
                "content_type": "video/mp4",
                "production_status": EpisodeSceneStatus.READY,
            })
            scenes = list(record.scenes); scenes[index] = updated_scene
            updated = record.model_copy(update={
                "status": EpisodeProductionStatus.PENDING,
                "scenes": tuple(scenes),
                "updated_at": self._clock(),
            })
            try:
                self._registry.update(updated)
            except ProductionRegistryError as error:
                if published:
                    destination.unlink(missing_ok=True)
                raise EpisodeLocalArtifactRegistryError("Local artifact state could not be stored.") from error
            return self._load(production_id)
        finally:
            temporary.unlink(missing_ok=True)

    def _probe_video(self, path: Path) -> None:
        try:
            self._probe.probe_video(path)
        except Exception as error:
            raise EpisodeLocalArtifactMediaError("Local artifact is not a valid supported video.") from error

    @staticmethod
    def _copy_and_hash(source: Path, temporary: Path) -> tuple[int, str]:
        digest = hashlib.sha256(); size = 0
        try:
            with source.open("rb") as input_stream, temporary.open("xb") as output_stream:
                while chunk := input_stream.read(1024 * 1024):
                    output_stream.write(chunk); digest.update(chunk); size += len(chunk)
                output_stream.flush(); os.fsync(output_stream.fileno())
        except OSError as error:
            raise EpisodeLocalArtifactSourceError("Local artifact could not be copied safely.") from error
        if size == 0:
            raise EpisodeLocalArtifactSourceError("Local artifact is empty.")
        return size, digest.hexdigest()

    @staticmethod
    def _hash_file(source: Path) -> tuple[int, str]:
        digest = hashlib.sha256(); size = 0
        try:
            with source.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk); size += len(chunk)
        except OSError as error:
            raise EpisodeLocalArtifactSourceError("Local artifact could not be read safely.") from error
        if size == 0:
            raise EpisodeLocalArtifactSourceError("Local artifact is empty.")
        return size, digest.hexdigest()

    def _load(self, production_id: str) -> ProductionRecord:
        try:
            return self._registry.load(production_id)
        except ProductionRegistryNotFoundError as error:
            raise EpisodeLocalArtifactProductionNotFoundError("Production was not found.") from error
        except ProductionRegistryError as error:
            raise EpisodeLocalArtifactRegistryError("Production registry could not be read safely.") from error

    @staticmethod
    def _scene_index(record: ProductionRecord, scene_id: str) -> int:
        for index, scene in enumerate(record.scenes):
            if scene.scene_id == scene_id:
                return index
        raise EpisodeLocalArtifactSceneNotFoundError("Production scene was not found.")
