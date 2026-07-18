from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from app.models import GenerationTaskStatus, VideoGenerationRequest
from app.services import VideoEngineError, VideoEngineTaskFailedError, VideoPollingPolicy
from app.timeline import (TimelineMediaValidationError, TimelineMediaValidator, TimelineOutput,
                          TimelineRendererError, TimelineScene, TimelineTransition, VideoTimeline,
                          build_render_plan)

from .contracts import (EpisodeProductionRequest, EpisodeProductionResult, EpisodeProductionStatus,
                        EpisodeSceneResult, ProductionRecord)
from .registry import (ProductionRegistry, ProductionRegistryConflictError, ProductionRegistryError,
                       ProductionRegistryNotFoundError, utc_now)


class EpisodeProductionError(RuntimeError): pass
class EpisodeProductionInvalidRequestError(EpisodeProductionError): pass
class EpisodeProductionConflictError(EpisodeProductionError): pass
class EpisodeProductionNotFoundError(EpisodeProductionError): pass
class EpisodeProductionRegistryError(EpisodeProductionError): pass
class EpisodeSceneSubmissionError(EpisodeProductionError): pass
class EpisodeScenePollingError(EpisodeProductionError): pass
class EpisodeProviderSceneFailedError(EpisodeProductionError): pass
class EpisodeSceneDownloadError(EpisodeProductionError): pass
class EpisodeSceneArtifactMissingError(EpisodeProductionError): pass
class EpisodeTimelineConstructionError(EpisodeProductionError): pass
class EpisodeTimelineValidationError(EpisodeProductionError): pass
class EpisodeRenderPlanError(EpisodeProductionError): pass
class EpisodeFinalRenderError(EpisodeProductionError): pass
class EpisodeUnsupportedProductionStateError(EpisodeProductionError): pass


class EpisodeProductionOrchestrator:
    def __init__(self, video_engine, timeline_renderer, production_registry: ProductionRegistry, probe,
                 *, clock: Callable = utc_now) -> None:
        self._video_engine = video_engine
        self._renderer = timeline_renderer
        self._registry = production_registry
        self._validator = TimelineMediaValidator(probe)
        self._clock = clock
        self._requests: dict[str, tuple[VideoGenerationRequest, ...]] = {}
        self._request_contracts: dict[str, EpisodeProductionRequest] = {}

    def produce(self, request: EpisodeProductionRequest, polling_policy: VideoPollingPolicy) -> EpisodeProductionResult:
        try:
            request = EpisodeProductionRequest.model_validate(request)
        except ValidationError as error:
            raise EpisodeProductionInvalidRequestError("Episode production request is invalid.") from error
        if self._registry.exists(request.production_id):
            raise EpisodeProductionConflictError("Episode production already exists.")
        now = self._clock()
        scenes = tuple(EpisodeSceneResult(scene_id=f"scene-{index + 1:04d}", order=index) for index in range(len(request.video_requests)))
        record = ProductionRecord(production_id=request.production_id, status=EpisodeProductionStatus.PENDING,
                                  provider=request.provider, scenes=scenes, created_at=now, updated_at=now)
        try:
            self._registry.create(record)
        except ProductionRegistryConflictError as error:
            raise EpisodeProductionConflictError("Episode production already exists.") from error
        except ProductionRegistryError as error:
            raise EpisodeProductionRegistryError("Episode production could not be stored.") from error
        self._requests[request.production_id] = request.video_requests
        self._request_contracts[request.production_id] = request
        return self._execute(request, polling_policy)

    def resume(self, production_id: str, polling_policy: VideoPollingPolicy) -> EpisodeProductionResult:
        try:
            record = self._registry.load(production_id)
        except ProductionRegistryNotFoundError as error:
            raise EpisodeProductionNotFoundError("Episode production was not found.") from error
        except ProductionRegistryError as error:
            raise EpisodeProductionRegistryError("Episode production could not be loaded.") from error
        if record.status == EpisodeProductionStatus.SUCCEEDED:
            return self._result(record)
        request = self._request_contracts.get(production_id)
        if request is None:
            if all(scene.local_path is not None for scene in record.scenes):
                raise EpisodeUnsupportedProductionStateError("Resume requires the original prompt-free production request paths and transition policy.")
            raise EpisodeUnsupportedProductionStateError("Unsubmitted scenes require the original production request.")
        return self._execute(request, polling_policy)

    def _execute(self, request: EpisodeProductionRequest, policy: VideoPollingPolicy) -> EpisodeProductionResult:
        try:
            record = self._set_status(self._registry.load(request.production_id), EpisodeProductionStatus.GENERATING)
            for index, generation_request in enumerate(request.video_requests):
                scene = record.scenes[index]
                if scene.local_path is not None:
                    if not scene.local_path.is_file():
                        raise EpisodeSceneArtifactMissingError(f"Local artifact for {scene.scene_id} is missing.")
                    continue
                destination = request.scene_output_directory / f"scene-{index + 1:04d}.mp4"
                if scene.provider_task_id is None:
                    try:
                        task = self._video_engine.submit(generation_request, provider=request.provider)
                    except VideoEngineError as error:
                        raise EpisodeSceneSubmissionError(f"Scene {scene.scene_id} submission failed.") from error
                    scene = scene.model_copy(update={"provider_task_id": task.provider_task_id, "normalized_status": task.normalized_status})
                    record = self._replace_scene(record, index, scene)
                try:
                    completed = self._video_engine.resume(scene.provider_task_id, destination, policy)
                except VideoEngineTaskFailedError as error:
                    failed = scene.model_copy(update={"normalized_status": GenerationTaskStatus.FAILED})
                    self._replace_scene(record, index, failed)
                    raise EpisodeProviderSceneFailedError(f"Scene {scene.scene_id} failed.") from error
                except VideoEngineError as error:
                    raise EpisodeScenePollingError(f"Scene {scene.scene_id} could not complete.") from error
                if completed.artifact is None:
                    raise EpisodeSceneDownloadError(f"Scene {scene.scene_id} has no downloaded artifact.")
                artifact = completed.artifact
                scene = scene.model_copy(update={"normalized_status": completed.normalized_status,
                    "local_path": artifact.local_path, "artifact_id": artifact.artifact_id, "sha256": artifact.sha256})
                record = self._replace_scene(record, index, scene)

            record = self._set_status(record, EpisodeProductionStatus.ASSEMBLING)
            timeline = self._timeline(request, record)
            try:
                validated = self._validator.validate(timeline)
            except TimelineMediaValidationError as error:
                raise EpisodeTimelineValidationError("Episode timeline media validation failed.") from error
            try:
                plan = build_render_plan(validated)
            except Exception as error:
                raise EpisodeRenderPlanError("Episode render plan could not be built.") from error
            try:
                final = self._renderer.render(plan)
            except TimelineRendererError as error:
                raise EpisodeFinalRenderError("Episode final render failed.") from error
            record = record.model_copy(update={"final_artifact": final})
            self._registry.update(record)
            record = self._set_status(record, EpisodeProductionStatus.SUCCEEDED)
            return self._result(record)
        except EpisodeProductionError:
            try:
                current = self._registry.load(request.production_id)
                if current.status != EpisodeProductionStatus.SUCCEEDED:
                    self._set_status(current, EpisodeProductionStatus.FAILED)
            except ProductionRegistryError:
                pass
            raise

    def _timeline(self, request: EpisodeProductionRequest, record: ProductionRecord) -> VideoTimeline:
        try:
            scenes = []
            for index, scene in enumerate(record.scenes):
                transition = None if index == len(record.scenes) - 1 else TimelineTransition(
                    kind=request.transition_policy.kind, duration_seconds=request.transition_policy.duration_seconds)
                scenes.append(TimelineScene(scene_id=scene.scene_id, source_path=scene.local_path,
                                            order=scene.order, transition_to_next=transition))
            return VideoTimeline(timeline_id=request.production_id, scenes=tuple(scenes),
                                 output=TimelineOutput(destination=request.final_output_path, workspace=request.media_workspace))
        except Exception as error:
            raise EpisodeTimelineConstructionError("Episode timeline could not be constructed.") from error

    def _replace_scene(self, record: ProductionRecord, index: int, scene: EpisodeSceneResult) -> ProductionRecord:
        scenes = list(record.scenes); scenes[index] = scene
        updated = record.model_copy(update={"scenes": tuple(scenes), "updated_at": self._clock()})
        self._registry.update(updated)
        return updated

    def _set_status(self, record: ProductionRecord, status: EpisodeProductionStatus) -> ProductionRecord:
        updated = record.model_copy(update={"status": status, "updated_at": self._clock()})
        self._registry.update(updated)
        return updated

    @staticmethod
    def _result(record: ProductionRecord) -> EpisodeProductionResult:
        return EpisodeProductionResult(production_id=record.production_id, status=record.status,
                                       scenes=record.scenes, final_artifact=record.final_artifact)
