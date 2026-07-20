from dataclasses import dataclass
from pathlib import Path

from .contracts import EpisodeProductionStatus, EpisodeSceneStatus
from .registry import ProductionRegistry


@dataclass(frozen=True)
class EpisodeSceneSummary:
    scene_id: str
    production_status: EpisodeSceneStatus
    provider_status: str | None
    provider_task_id: str | None
    local_artifact: Path | None


@dataclass(frozen=True)
class EpisodeProductionSummary:
    production_id: str
    status: EpisodeProductionStatus
    scenes: tuple[EpisodeSceneSummary, ...]
    final_artifact_present: bool
    final_path: Path | None


class EpisodeProductionSummaryService:
    """Read-only durable production projection with no provider dependencies."""
    def __init__(self, registry: ProductionRegistry) -> None:
        self._registry=registry

    def load(self, production_id: str) -> EpisodeProductionSummary:
        record=self._registry.load(production_id)
        return EpisodeProductionSummary(production_id=record.production_id,status=record.status,
            scenes=tuple(EpisodeSceneSummary(scene_id=scene.scene_id,production_status=scene.production_status,
                provider_status=scene.normalized_status.value if scene.normalized_status else None,
                provider_task_id=scene.provider_task_id,local_artifact=scene.local_path) for scene in record.scenes),
            final_artifact_present=record.final_artifact is not None,
            final_path=record.final_artifact.local_path if record.final_artifact else None)
