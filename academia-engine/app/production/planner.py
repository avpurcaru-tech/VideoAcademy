from pathlib import Path

from app.models import DirectorPlan, VideoGenerationRequest
from app.prompts import PromptBuilder

from .contracts import EpisodeProductionRequest, EpisodeTransitionPolicy
from .request_reference import (GenerationRequestConflictError, GenerationRequestNotFoundError,
                                GenerationRequestReference, GenerationRequestResolverError, GenerationRequestStore)


class EpisodeProductionPlanningError(RuntimeError): pass
class EpisodeProductionSceneOrderError(EpisodeProductionPlanningError): pass
class EpisodeProductionRequestConflictError(EpisodeProductionPlanningError): pass


class EpisodeProductionPlanner:
    """Provider-neutral DirectorPlan -> durable semantic generation request integration."""
    def __init__(self, prompt_builder: PromptBuilder, request_store: GenerationRequestStore) -> None:
        self._prompt_builder = prompt_builder
        self._request_store = request_store

    def plan(self, director_plan: DirectorPlan, production_id: str, scene_output_directory: Path,
             workspace: Path, destination: Path, *, provider: str = "default",
             transition: EpisodeTransitionPolicy | None = None) -> EpisodeProductionRequest:
        numbers = [scene.scene_number for scene in director_plan.scenes]
        if len(numbers) != len(set(numbers)) or sorted(numbers) != list(range(1, len(numbers) + 1)):
            raise EpisodeProductionSceneOrderError("Director scene numbers must be unique and contiguous from one.")
        ordered = director_plan.model_copy(update={"scenes": sorted(director_plan.scenes, key=lambda scene: scene.scene_number)})
        video_requests = self._prompt_builder.build(ordered)
        if len(video_requests) != len(ordered.scenes):
            raise EpisodeProductionPlanningError("Prompt builder returned an inconsistent scene count.")
        references = tuple(GenerationRequestReference(reference_id=f"{production_id}-scene-{index + 1:04d}") for index in range(len(video_requests)))
        generation_requests = tuple(VideoGenerationRequest(request_id=reference.reference_id, video_request=video_request)
                                    for reference, video_request in zip(references, video_requests, strict=True))
        for reference, request in zip(references, generation_requests, strict=True):
            try:
                existing = self._request_store.resolve(reference)
            except GenerationRequestNotFoundError:
                continue
            except GenerationRequestResolverError as error:
                raise EpisodeProductionPlanningError("Generation request store could not be inspected.") from error
            if existing != request:
                raise EpisodeProductionRequestConflictError("A deterministic generation request reference conflicts.")
        request = EpisodeProductionRequest(production_id=production_id, video_requests=generation_requests,
            generation_request_references=references,
            source_scene_ids=tuple(f"{director_plan.episode_id}-scene-{scene.scene_number:04d}" for scene in ordered.scenes),
            provider=provider, scene_output_directory=scene_output_directory,
            final_output_path=destination, media_workspace=workspace,
            transition_policy=transition or EpisodeTransitionPolicy(kind="cut"))
        for reference, generation_request in zip(references, generation_requests, strict=True):
            try:
                self._request_store.create(reference, generation_request)
            except GenerationRequestConflictError as error:
                raise EpisodeProductionRequestConflictError("A deterministic generation request reference conflicts.") from error
        return request


class EpisodeGenerationService:
    def __init__(self, planner: EpisodeProductionPlanner, orchestrator) -> None:
        self._planner = planner; self._orchestrator = orchestrator

    def plan_only(self, director_plan: DirectorPlan, **configuration) -> EpisodeProductionRequest:
        return self._planner.plan(director_plan, **configuration)

    def plan_and_produce(self, director_plan: DirectorPlan, polling_policy, **configuration):
        request = self.plan_only(director_plan, **configuration)
        return self._orchestrator.produce(request, polling_policy)
