import hashlib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .contracts import EpisodeProductionStatus, EpisodeSceneStatus, ProductionRecord
from .registry import ProductionRegistry, ProductionRegistryError, ProductionRegistryNotFoundError, utc_now


class ArtifactIntegrityState(str, Enum):
    VALID="valid"
    MISSING="missing"
    NOT_FILE="not_file"
    EMPTY="empty"
    SIZE_MISMATCH="size_mismatch"
    HASH_MISMATCH="hash_mismatch"
    METADATA_MISSING="metadata_missing"


@dataclass(frozen=True)
class ArtifactIntegrityResult:
    state: ArtifactIntegrityState
    local_path: Path | None

    @property
    def valid(self) -> bool: return self.state == ArtifactIntegrityState.VALID


@dataclass(frozen=True)
class SceneIntegrityResult:
    scene_id: str
    artifact: ArtifactIntegrityResult


@dataclass(frozen=True)
class ProductionIntegrityReport:
    production_id: str
    status: EpisodeProductionStatus
    scenes: tuple[SceneIntegrityResult,...]
    final_artifact: ArtifactIntegrityResult

    @property
    def valid(self) -> bool:
        return all(scene.artifact.valid for scene in self.scenes) and self.final_artifact.valid


class ProductionIntegrityService:
    """Read-only local artifact verification; intentionally performs no media probing."""
    def verify_artifact(self, artifact_metadata) -> ArtifactIntegrityResult:
        path=getattr(artifact_metadata,"local_path",None) if artifact_metadata is not None else None
        size=getattr(artifact_metadata,"byte_size",None) if artifact_metadata is not None else None
        sha256=getattr(artifact_metadata,"sha256",None) if artifact_metadata is not None else None
        if path is None or size is None or sha256 is None:
            return ArtifactIntegrityResult(ArtifactIntegrityState.METADATA_MISSING,Path(path) if path else None)
        local_path=Path(path)
        if not local_path.exists(): return ArtifactIntegrityResult(ArtifactIntegrityState.MISSING,local_path)
        if not local_path.is_file(): return ArtifactIntegrityResult(ArtifactIntegrityState.NOT_FILE,local_path)
        actual_size=local_path.stat().st_size
        if actual_size == 0: return ArtifactIntegrityResult(ArtifactIntegrityState.EMPTY,local_path)
        if actual_size != size: return ArtifactIntegrityResult(ArtifactIntegrityState.SIZE_MISMATCH,local_path)
        digest=hashlib.sha256()
        with local_path.open("rb") as stream:
            while chunk:=stream.read(1024*1024): digest.update(chunk)
        if digest.hexdigest()!=sha256: return ArtifactIntegrityResult(ArtifactIntegrityState.HASH_MISMATCH,local_path)
        return ArtifactIntegrityResult(ArtifactIntegrityState.VALID,local_path)

    def verify_scene(self, scene) -> ArtifactIntegrityResult:
        has_metadata=any(getattr(scene,name,None) is not None for name in ("local_path","byte_size","sha256"))
        if scene.production_status == EpisodeSceneStatus.READY or has_metadata:
            return self.verify_artifact(scene)
        return ArtifactIntegrityResult(ArtifactIntegrityState.VALID,None)

    def verify_production(self, record: ProductionRecord) -> ProductionIntegrityReport:
        scenes=tuple(SceneIntegrityResult(scene.scene_id,self.verify_scene(scene)) for scene in record.scenes)
        if record.final_artifact is not None:
            final=self.verify_artifact(record.final_artifact)
        elif record.status == EpisodeProductionStatus.SUCCEEDED:
            final=ArtifactIntegrityResult(ArtifactIntegrityState.METADATA_MISSING,None)
        else:
            final=ArtifactIntegrityResult(ArtifactIntegrityState.VALID,None)
        return ProductionIntegrityReport(record.production_id,record.status,scenes,final)


class ArtifactMetadataReconciliationError(RuntimeError): pass
class ArtifactMetadataProductionNotFoundError(ArtifactMetadataReconciliationError): pass
class ArtifactMetadataSceneNotFoundError(ArtifactMetadataReconciliationError): pass
class ArtifactMetadataLocalFileError(ArtifactMetadataReconciliationError): pass
class ArtifactMetadataRegistryError(ArtifactMetadataReconciliationError): pass


class ProductionArtifactMetadataReconciler:
    """Explicit local-only repair for incomplete durable scene artifact metadata."""
    def __init__(self, registry: ProductionRegistry, *, clock=utc_now) -> None:
        self._registry=registry; self._clock=clock

    def reconcile_scene(self, production_id: str, scene_id: str) -> ProductionRecord:
        try: record=self._registry.load(production_id)
        except ProductionRegistryNotFoundError as error:
            raise ArtifactMetadataProductionNotFoundError("Production was not found.") from error
        except ProductionRegistryError as error:
            raise ArtifactMetadataRegistryError("Production registry could not be read safely.") from error
        index=next((index for index,scene in enumerate(record.scenes) if scene.scene_id==scene_id),None)
        if index is None: raise ArtifactMetadataSceneNotFoundError("Production scene was not found.")
        scene=record.scenes[index]
        if scene.local_path is None: raise ArtifactMetadataLocalFileError("Scene has no durable local path.")
        path=Path(scene.local_path)
        if not path.exists(): raise ArtifactMetadataLocalFileError("Scene artifact file is missing.")
        if not path.is_file(): raise ArtifactMetadataLocalFileError("Scene artifact path is not a regular file.")
        size=path.stat().st_size
        if size<=0: raise ArtifactMetadataLocalFileError("Scene artifact file is empty.")
        digest=hashlib.sha256()
        try:
            with path.open("rb") as stream:
                while chunk:=stream.read(1024*1024): digest.update(chunk)
        except OSError as error:
            raise ArtifactMetadataLocalFileError("Scene artifact file could not be read safely.") from error
        sha256=digest.hexdigest()
        updated_scene=scene.model_copy(update={
            "artifact_id": scene.artifact_id or f"local:{sha256}",
            "byte_size": size,
            "sha256": sha256,
            "content_type": scene.content_type or "video/mp4",
            "production_status": EpisodeSceneStatus.READY,
        })
        scenes=list(record.scenes); scenes[index]=updated_scene
        updated=record.model_copy(update={"scenes":tuple(scenes),"updated_at":self._clock()})
        try: self._registry.update(updated)
        except ProductionRegistryError as error:
            raise ArtifactMetadataRegistryError("Reconciled artifact metadata could not be stored.") from error
        try: return self._registry.load(production_id)
        except ProductionRegistryError as error:
            raise ArtifactMetadataRegistryError("Reconciled artifact metadata could not be reloaded.") from error
