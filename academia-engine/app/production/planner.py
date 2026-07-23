from pathlib import Path

from pydantic import ValidationError

from app.models import DirectorPlan, VideoGenerationRequest
from app.prompts import PromptBuilder

from .contracts import EpisodeProductionRequest, EpisodeTransitionPolicy
from .duration_policy import SceneDurationPolicy
from .request_reference import (GenerationRequestConflictError, GenerationRequestCorruptedError, GenerationRequestNotFoundError,
                                GenerationRequestReference, GenerationRequestResolverError, GenerationRequestStore)


class EpisodeProductionPlanningError(RuntimeError): pass
class EpisodeProductionSceneOrderError(EpisodeProductionPlanningError): pass
class EpisodeProductionDuplicateSceneOrderError(EpisodeProductionSceneOrderError): pass
class EpisodeProductionNonContiguousSceneOrderError(EpisodeProductionSceneOrderError): pass
class EpisodeProductionRequestConflictError(EpisodeProductionPlanningError): pass
class EpisodeProductionRequestStoreCorruptedError(EpisodeProductionPlanningError): pass
class EpisodeProductionPromptBuilderError(EpisodeProductionPlanningError): pass
class EpisodeProductionSemanticVideoRequestError(EpisodeProductionPlanningError):
    def __init__(self, validation_error: ValidationError): self.validation_error = validation_error
class EpisodeProductionVideoRequestError(EpisodeProductionPlanningError):
    def __init__(self, validation_error: ValidationError): self.validation_error = validation_error
class EpisodeProductionContractError(EpisodeProductionPlanningError):
    def __init__(self, validation_error: ValidationError): self.validation_error = validation_error
class EpisodeProductionReferenceError(EpisodeProductionPlanningError): pass


class EpisodeProductionPlanner:
    """Provider-neutral DirectorPlan -> durable semantic generation request integration."""
    def __init__(self, prompt_builder: PromptBuilder, request_store: GenerationRequestStore,
                 duration_policy: SceneDurationPolicy | None = None) -> None:
        self._prompt_builder = prompt_builder
        self._request_store = request_store
        self._duration_policy = duration_policy or SceneDurationPolicy(15)

    def plan(self, director_plan: DirectorPlan, production_id: str, scene_output_directory: Path,
             workspace: Path, destination: Path, *, provider: str = "default",
             transition: EpisodeTransitionPolicy | None = None) -> EpisodeProductionRequest:
        request = self.preflight(director_plan, production_id, scene_output_directory, workspace, destination,
                                 provider=provider, transition=transition)
        self.persist(request)
        return request

    def preflight(self, director_plan: DirectorPlan, production_id: str, scene_output_directory: Path,
                  workspace: Path, destination: Path, *, provider: str = "default",
                  transition: EpisodeTransitionPolicy | None = None) -> EpisodeProductionRequest:
        from app.storyboard import CreativeStoryboard
        if isinstance(director_plan,CreativeStoryboard):
            return self._preflight_storyboard(director_plan,production_id,scene_output_directory,workspace,
                destination,provider=provider,transition=transition)
        numbers = [scene.scene_number for scene in director_plan.scenes]
        if len(numbers) != len(set(numbers)):
            raise EpisodeProductionDuplicateSceneOrderError("Director scene numbers must be unique.")
        if sorted(numbers) != list(range(1, len(numbers) + 1)):
            raise EpisodeProductionNonContiguousSceneOrderError("Director scene numbers must be contiguous from one.")
        ordered = director_plan.model_copy(update={"scenes": sorted(director_plan.scenes, key=lambda scene: scene.scene_number)})
        try:
            video_requests = self._prompt_builder.build(ordered)
            video_requests = tuple(self._duration_policy.apply_execution_duration(value) for value in video_requests)
        except ValidationError as error:
            raise EpisodeProductionSemanticVideoRequestError(error) from error
        except Exception as error:
            raise EpisodeProductionPromptBuilderError("Prompt builder could not construct semantic video requests.") from error
        if len(video_requests) != len(ordered.scenes):
            raise EpisodeProductionPlanningError("Prompt builder returned an inconsistent scene count.")
        try:
            references = tuple(GenerationRequestReference(reference_id=f"{production_id}-scene-{index + 1:04d}") for index in range(len(video_requests)))
        except ValidationError as error:
            raise EpisodeProductionReferenceError("Deterministic request reference is invalid.") from error
        try:
            generation_requests = tuple(VideoGenerationRequest(request_id=reference.reference_id, video_request=video_request)
                                        for reference, video_request in zip(references, video_requests, strict=True))
        except ValidationError as error:
            raise EpisodeProductionVideoRequestError(error) from error
        for reference, request in zip(references, generation_requests, strict=True):
            try:
                existing = self._request_store.resolve(reference)
            except GenerationRequestNotFoundError:
                continue
            except GenerationRequestCorruptedError as error:
                raise EpisodeProductionRequestStoreCorruptedError("Generation request store record is corrupted.") from error
            except GenerationRequestResolverError as error:
                raise EpisodeProductionPlanningError("Generation request store could not be inspected.") from error
            if existing != request:
                raise EpisodeProductionRequestConflictError("A deterministic generation request reference conflicts.")
        try:
            request = EpisodeProductionRequest(production_id=production_id, video_requests=generation_requests,
                generation_request_references=references,
                source_scene_ids=tuple(f"{director_plan.episode_id}-scene-{scene.scene_number:04d}" for scene in ordered.scenes),
                provider=provider, scene_output_directory=scene_output_directory,
                final_output_path=destination, media_workspace=workspace,
                transition_policy=transition or EpisodeTransitionPolicy(kind="cut"))
        except ValidationError as error:
            raise EpisodeProductionContractError(error) from error
        return request

    def _preflight_storyboard(self,storyboard,production_id,scene_output_directory,workspace,destination,
                              *,provider,transition):
        from .storyboard_video_planner import StoryboardVideoPlanner,StoryboardVideoPlanningError
        try: generation_requests=StoryboardVideoPlanner(self._duration_policy).build(storyboard,production_id)
        except StoryboardVideoPlanningError as error:
            raise EpisodeProductionPlanningError("Storyboard could not construct semantic video requests.") from error
        referenced=tuple(request.scene_visual_reference is not None for request in generation_requests)
        if any(referenced) and not all(referenced):
            raise EpisodeProductionPlanningError("Mixed text-only and image-reference scenes require separate productions.")
        if all(referenced):
            provider="kling_image_to_video"
            generation_requests=tuple(request.model_copy(update={"video_request":request.video_request.model_copy(
                update={"duration_seconds":10})}) for request in generation_requests)
        references=tuple(GenerationRequestReference(reference_id=request.request_id) for request in generation_requests)
        for reference,request in zip(references,generation_requests,strict=True):
            try: existing=self._request_store.resolve(reference)
            except GenerationRequestNotFoundError: continue
            except GenerationRequestCorruptedError as error:
                raise EpisodeProductionRequestStoreCorruptedError("Generation request store record is corrupted.") from error
            except GenerationRequestResolverError as error:
                raise EpisodeProductionPlanningError("Generation request store could not be inspected.") from error
            if existing!=request: raise EpisodeProductionRequestConflictError("A deterministic generation request reference conflicts.")
        try:
            return EpisodeProductionRequest(production_id=production_id,video_requests=generation_requests,
                generation_request_references=references,source_scene_ids=tuple(section.section_id for section in storyboard.sections),
                provider=provider,scene_output_directory=scene_output_directory,final_output_path=destination,
                media_workspace=workspace,transition_policy=transition or EpisodeTransitionPolicy(kind="cut"))
        except ValidationError as error: raise EpisodeProductionContractError(error) from error

    def persist(self, request: EpisodeProductionRequest) -> None:
        for reference, generation_request in zip(request.generation_request_references, request.video_requests, strict=True):
            try:
                self._request_store.create(reference, generation_request)
            except GenerationRequestConflictError as error:
                raise EpisodeProductionRequestConflictError("A deterministic generation request reference conflicts.") from error


class EpisodeGenerationService:
    def __init__(self, planner: EpisodeProductionPlanner, orchestrator) -> None:
        self._planner = planner; self._orchestrator = orchestrator

    def plan_only(self, director_plan: DirectorPlan, **configuration) -> EpisodeProductionRequest:
        return self._planner.plan(director_plan, **configuration)

    def plan_and_produce(self, director_plan: DirectorPlan, polling_policy, **configuration):
        request = self.plan_only(director_plan, **configuration)
        return self._orchestrator.produce(request, polling_policy)
    def produce_planned(self,request,polling_policy): return self._orchestrator.produce(request,polling_policy)
