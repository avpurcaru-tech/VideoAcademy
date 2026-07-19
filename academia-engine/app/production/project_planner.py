from pathlib import Path

from app.engines.director import DirectorEngine
from app.models import Episode

from .contracts import EpisodeProductionRequest, EpisodeTransitionPolicy
from .planner import EpisodeProductionPlanner


class EpisodeProjectPlanningError(RuntimeError): pass
class EpisodeProjectDirectionError(EpisodeProjectPlanningError): pass


class EpisodeProjectPlanner:
    """Deterministic Episode -> DirectorEngine -> EpisodeProductionPlanner composition."""
    def __init__(self, director_engine: DirectorEngine, production_planner: EpisodeProductionPlanner) -> None:
        self._director_engine = director_engine
        self._production_planner = production_planner

    def preflight(self, episode: Episode, production_id: str, scene_output_directory: Path,
                  workspace: Path, destination: Path, *, provider: str = "default",
                  transition: EpisodeTransitionPolicy | None = None) -> EpisodeProductionRequest:
        try:
            director_plan = self._director_engine.create_plan(episode)
        except Exception as error:
            raise EpisodeProjectDirectionError("Episode could not be converted into a DirectorPlan.") from error
        return self._production_planner.preflight(director_plan, production_id, scene_output_directory,
                                                  workspace, destination, provider=provider, transition=transition)

    def persist(self, request: EpisodeProductionRequest) -> None:
        self._production_planner.persist(request)

    def plan_episode(self, episode: Episode, production_id: str, scene_output_directory: Path,
                     workspace: Path, destination: Path, *, provider: str = "default",
                     transition: EpisodeTransitionPolicy | None = None) -> EpisodeProductionRequest:
        request = self.preflight(episode, production_id, scene_output_directory, workspace, destination,
                                 provider=provider, transition=transition)
        self.persist(request)
        return request


class EpisodeProjectGenerationService:
    def __init__(self, project_planner: EpisodeProjectPlanner, orchestrator) -> None:
        self._planner=project_planner; self._orchestrator=orchestrator

    def plan_and_produce(self, episode: Episode, polling_policy, **configuration):
        request=self._planner.plan_episode(episode,**configuration)
        return self.produce_planned(request,polling_policy)

    def produce_planned(self, request: EpisodeProductionRequest, polling_policy):
        return self._orchestrator.produce(request,polling_policy)
