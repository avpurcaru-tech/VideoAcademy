from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from app.models import GenerationTaskStatus, VideoGenerationRequest
from app.services import VideoEngineError, VideoEngineTaskFailedError, VideoPollingPolicy
from app.services import UnknownVideoProviderError, VideoEngineRegistryError, VideoProviderOperationError
from app.providers import KlingUnsupportedConfigurationError
from app.timeline import (TimelineMediaValidationError, TimelineMediaValidator, TimelineOutput,
                          TimelineRendererError, TimelineScene, TimelineTransition, VideoTimeline,
                          build_render_plan)

from .contracts import (EpisodeProductionRequest, EpisodeProductionResult, EpisodeProductionStatus,
                        EpisodeSceneResult, EpisodeSceneStatus, ProductionRecord)
from .contracts import ProductionFailureStage
from .registry import (ProductionRegistry, ProductionRegistryConflictError, ProductionRegistryError,
                       ProductionRegistryNotFoundError, utc_now)
from .request_reference import (GenerationRequestResolver, GenerationRequestResolverError,
                                GenerationRequestNotFoundError, GenerationRequestCorruptedError)
from .integrity import ArtifactIntegrityState, ProductionIntegrityService


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
class EpisodeGenerationRequestResolutionError(EpisodeProductionError): pass
class EpisodeGenerationRequestMissingError(EpisodeGenerationRequestResolutionError): pass
class EpisodeGenerationRequestCorruptedError(EpisodeGenerationRequestResolutionError): pass
class EpisodeGenerationSettingsMismatchError(EpisodeGenerationRequestResolutionError): pass
class EpisodeProviderUnavailableError(EpisodeProductionError): pass
class EpisodeProviderConfigurationError(EpisodeProductionError): pass
class EpisodeSubmitRejectedError(EpisodeSceneSubmissionError): pass
class EpisodeSubmitResponseParsingError(EpisodeSceneSubmissionError): pass
class ProductionArtifactIntegrityError(EpisodeProductionError): pass
class ProductionFinalArtifactMissingError(ProductionArtifactIntegrityError): pass
class ProductionSceneArtifactIntegrityError(ProductionArtifactIntegrityError): pass


class EpisodeProductionOrchestrator:
    def __init__(self, video_engine, timeline_renderer, production_registry: ProductionRegistry, probe,
                 request_resolver: GenerationRequestResolver, *, clock: Callable = utc_now,
                 integrity_service: ProductionIntegrityService | None = None) -> None:
        self._video_engine = video_engine
        self._renderer = timeline_renderer
        self._registry = production_registry
        self._validator = TimelineMediaValidator(probe)
        self._clock = clock
        self._request_resolver = request_resolver
        self._integrity = integrity_service or ProductionIntegrityService()

    def produce(self, request: EpisodeProductionRequest, polling_policy: VideoPollingPolicy) -> EpisodeProductionResult:
        try:
            request = EpisodeProductionRequest.model_validate(request)
        except ValidationError as error:
            raise EpisodeProductionInvalidRequestError("Episode production request is invalid.") from error
        if self._registry.exists(request.production_id):
            raise EpisodeProductionConflictError("Episode production already exists.")
        for reference, expected in zip(request.generation_request_references, request.video_requests, strict=True):
            try:
                resolved = self._request_resolver.resolve(reference)
            except Exception as error:
                raise EpisodeGenerationRequestResolutionError("A generation request reference could not be resolved.") from error
            if not isinstance(resolved, VideoGenerationRequest) or resolved != expected:
                raise EpisodeGenerationRequestResolutionError("A generation request reference resolved inconsistently.")
        now = self._clock()
        scenes = tuple(EpisodeSceneResult(scene_id=f"scene-{index + 1:04d}", order=index,
            source_scene_id=request.source_scene_ids[index] if request.source_scene_ids else None,
            generation_request_reference=request.generation_request_references[index]) for index in range(len(request.video_requests)))
        record = ProductionRecord(production_id=request.production_id, status=EpisodeProductionStatus.PENDING,
                                  provider=request.provider, scenes=scenes,
                                  scene_output_directory=request.scene_output_directory,
                                  final_output_path=request.final_output_path, media_workspace=request.media_workspace,
                                  transition_policy=request.transition_policy, created_at=now, updated_at=now)
        try:
            self._registry.create(record)
        except ProductionRegistryConflictError as error:
            raise EpisodeProductionConflictError("Episode production already exists.") from error
        except ProductionRegistryError as error:
            raise EpisodeProductionRegistryError("Episode production could not be stored.") from error
        return self._execute(request.production_id, polling_policy)

    def resume(self, production_id: str, polling_policy: VideoPollingPolicy) -> EpisodeProductionResult:
        try:
            record = self._registry.load(production_id)
        except ProductionRegistryNotFoundError as error:
            raise EpisodeProductionNotFoundError("Episode production was not found.") from error
        except ProductionRegistryError as error:
            raise EpisodeProductionRegistryError("Episode production could not be loaded.") from error
        if record.status == EpisodeProductionStatus.SUCCEEDED:
            if record.final_artifact is None:
                raise ProductionFinalArtifactMissingError("Succeeded production final artifact metadata is missing.")
            integrity=self._integrity.verify_artifact(record.final_artifact)
            if integrity.state == ArtifactIntegrityState.MISSING:
                raise ProductionFinalArtifactMissingError("Succeeded production final artifact is missing.")
            if not integrity.valid:
                raise ProductionArtifactIntegrityError("Succeeded production final artifact failed integrity verification.")
            return self._result(record)
        return self._execute(production_id, polling_policy)

    def _execute(self, production_id: str, policy: VideoPollingPolicy) -> EpisodeProductionResult:
        try:
            record = self._registry.load(production_id)
            for scene in record.scenes:
                integrity=self._integrity.verify_scene(scene)
                if not integrity.valid:
                    raise ProductionSceneArtifactIntegrityError(f"Scene {scene.scene_id} artifact failed integrity verification.")
            record = self._set_status(record, EpisodeProductionStatus.GENERATING)
            for index in range(len(record.scenes)):
                scene = record.scenes[index]
                if scene.local_path is not None:
                    if not scene.local_path.is_file():
                        raise EpisodeSceneArtifactMissingError(f"Local artifact for {scene.scene_id} is missing.")
                    continue
                destination = record.scene_output_directory / f"scene-{index + 1:04d}.mp4"
                if scene.provider_task_id is None:
                    try:
                        generation_request = self._request_resolver.resolve(scene.generation_request_reference)
                        if not isinstance(generation_request, VideoGenerationRequest):
                            raise TypeError("Resolver returned the wrong request type.")
                    except GenerationRequestNotFoundError as error:
                        raise EpisodeGenerationRequestMissingError(f"Scene {scene.scene_id} request reference is missing.") from error
                    except GenerationRequestCorruptedError as error:
                        raise EpisodeGenerationRequestCorruptedError(f"Scene {scene.scene_id} request record is corrupted.") from error
                    except (GenerationRequestResolverError, ValidationError, TypeError) as error:
                        raise EpisodeGenerationRequestResolutionError(f"Scene {scene.scene_id} request is invalid.") from error
                    try:
                        task = self._video_engine.submit(generation_request, provider=record.provider)
                    except UnknownVideoProviderError as error:
                        raise EpisodeProviderUnavailableError(f"Scene {scene.scene_id} provider is unavailable.") from error
                    except VideoEngineRegistryError as error:
                        task_id=getattr(error,"provider_task_id",None)
                        if isinstance(task_id,str) and task_id:
                            scene=scene.model_copy(update={"provider_task_id":task_id,"production_status":EpisodeSceneStatus.GENERATING})
                            record=self._replace_scene(record,index,scene)
                        raise EpisodeProductionRegistryError(f"Scene {scene.scene_id} task registry persistence failed.") from error
                    except VideoEngineError as error:
                        cause=error.__cause__
                        if isinstance(cause,KlingUnsupportedConfigurationError):
                            raise EpisodeGenerationSettingsMismatchError(
                                f"Scene {scene.scene_id} request is incompatible with provider generation settings.") from error
                        if isinstance(cause,(ValueError,KeyError)):
                            raise EpisodeProviderConfigurationError(f"Scene {scene.scene_id} provider configuration is missing or invalid.") from error
                        category=type(cause).__name__.lower() if cause is not None else ""
                        if "malformed" in category or "parse" in category or "contract" in category:
                            task_id=getattr(cause,"provider_task_id",None)
                            if isinstance(task_id,str) and task_id:
                                scene=scene.model_copy(update={"provider_task_id":task_id,"production_status":EpisodeSceneStatus.GENERATING})
                                record=self._replace_scene(record,index,scene)
                            raise EpisodeSubmitResponseParsingError(f"Scene {scene.scene_id} submit response could not be parsed.") from error
                        raise EpisodeSubmitRejectedError(f"Scene {scene.scene_id} submit was rejected before a task ID was returned.") from error
                    scene = scene.model_copy(update={"provider_task_id": task.provider_task_id, "normalized_status": task.normalized_status})
                    scene = scene.model_copy(update={"production_status": EpisodeSceneStatus.GENERATING})
                    record = self._replace_scene(record, index, scene)
                try:
                    completed = self._video_engine.resume(scene.provider_task_id, destination, policy)
                except VideoEngineTaskFailedError as error:
                    failed = scene.model_copy(update={"normalized_status": GenerationTaskStatus.FAILED})
                    failed = failed.model_copy(update={"production_status": EpisodeSceneStatus.FAILED})
                    self._replace_scene(record, index, failed)
                    raise EpisodeProviderSceneFailedError(f"Scene {scene.scene_id} failed.") from error
                except VideoEngineError as error:
                    raise EpisodeScenePollingError(f"Scene {scene.scene_id} could not complete.") from error
                if completed.artifact is None:
                    raise EpisodeSceneDownloadError(f"Scene {scene.scene_id} has no downloaded artifact.")
                artifact = completed.artifact
                scene = scene.model_copy(update={"normalized_status": completed.normalized_status,
                    "local_path": artifact.local_path, "artifact_id": artifact.artifact_id,
                    "byte_size": artifact.byte_size, "sha256": artifact.sha256, "content_type": artifact.content_type,
                    "production_status": EpisodeSceneStatus.READY})
                record = self._replace_scene(record, index, scene)

            record = self._set_status(record, EpisodeProductionStatus.ASSEMBLING)
            timeline = self._timeline(record)
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
        except EpisodeProductionError as error:
            try:
                current = self._registry.load(production_id)
                if current.status != EpisodeProductionStatus.SUCCEEDED:
                    stage,category=self._failure_details(error)
                    scene_id=self._scene_id(error)
                    failed=current.model_copy(update={"status":EpisodeProductionStatus.FAILED,
                        "failed_scene_id":scene_id,"failure_stage":stage,"failure_category":category,
                        "safe_message":str(error),"updated_at":self._clock()})
                    self._registry.update(failed)
            except ProductionRegistryError:
                pass
            raise

    @staticmethod
    def _scene_id(error):
        message=str(error)
        for token in message.split():
            if token.startswith("scene-"): return token.rstrip(".:,")
        return None

    @staticmethod
    def _failure_details(error):
        mapping=(
            (EpisodeGenerationRequestMissingError,(ProductionFailureStage.VIDEO_REQUEST_RESOLUTION,"request_reference_missing")),
            (EpisodeGenerationRequestCorruptedError,(ProductionFailureStage.VIDEO_REQUEST_RESOLUTION,"request_record_corrupted")),
            (EpisodeGenerationSettingsMismatchError,(ProductionFailureStage.VIDEO_REQUEST_RESOLUTION,"request_generation_settings_mismatch")),
            (EpisodeGenerationRequestResolutionError,(ProductionFailureStage.VIDEO_REQUEST_RESOLUTION,"request_validation_failed")),
            (EpisodeProviderConfigurationError,(ProductionFailureStage.VIDEO_PROVIDER_CONFIGURATION,"provider_configuration_missing")),
            (EpisodeProviderUnavailableError,(ProductionFailureStage.VIDEO_PROVIDER_CONFIGURATION,"provider_unavailable")),
            (EpisodeSubmitResponseParsingError,(ProductionFailureStage.VIDEO_SUBMISSION,"submit_response_parsing_failed")),
            (EpisodeSubmitRejectedError,(ProductionFailureStage.VIDEO_SUBMISSION,"submit_rejected_before_task_creation")),
            (EpisodeScenePollingError,(ProductionFailureStage.VIDEO_POLLING,"provider_polling_failed")),
            (EpisodeSceneDownloadError,(ProductionFailureStage.VIDEO_DOWNLOAD,"artifact_download_failed")),
            (EpisodeProductionRegistryError,(ProductionFailureStage.REGISTRY_PERSISTENCE,"registry_persistence_failed")),
        )
        for kind,value in mapping:
            if isinstance(error,kind): return value
        return ProductionFailureStage.VIDEO_ASSEMBLY,"video_stage_failed"

    def _timeline(self, record: ProductionRecord) -> VideoTimeline:
        try:
            scenes = []
            for index, scene in enumerate(record.scenes):
                transition = None if index == len(record.scenes) - 1 else TimelineTransition(
                    kind=record.transition_policy.kind, duration_seconds=record.transition_policy.duration_seconds)
                scenes.append(TimelineScene(scene_id=scene.scene_id, source_path=scene.local_path,
                                            order=scene.order, transition_to_next=transition))
            return VideoTimeline(timeline_id=record.production_id, scenes=tuple(scenes),
                                 output=TimelineOutput(destination=record.final_output_path, workspace=record.media_workspace))
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
