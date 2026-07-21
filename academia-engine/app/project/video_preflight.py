from pydantic import ValidationError

from app.models import VideoGenerationRequest
from app.production import (GenerationRequestCorruptedError,GenerationRequestNotFoundError,
                            GenerationRequestResolverError,GenerationRequestStore,ProductionRegistry)
from app.config import KlingProviderConfigurationError
from app.providers import KlingProviderRegistry,KlingProviderRegistryError,KlingUnsupportedConfigurationError


class ProjectVideoPreflightError(RuntimeError):
    def __init__(self,category,safe_message,scene_id=None):
        super().__init__(safe_message); self.category=category; self.scene_id=scene_id


class ProjectVideoPreflightService:
    def __init__(self,project_registry,production_registry=None,request_store=None,environ=None,provider_registry=None):
        self._projects=project_registry
        self._productions=production_registry or ProductionRegistry()
        self._requests=request_store or GenerationRequestStore()
        self._environ=environ; self._providers=provider_registry or KlingProviderRegistry()

    def inspect(self,project_id):
        project=self._projects.load(project_id)
        production=self._productions.load(project.video_production_id)
        readiness=[]
        for scene in production.scenes:
            try: request=self._requests.resolve(scene.generation_request_reference)
            except GenerationRequestNotFoundError as error:
                raise ProjectVideoPreflightError("request_reference_missing","Generation request reference is missing.",scene.scene_id) from error
            except GenerationRequestCorruptedError as error:
                raise ProjectVideoPreflightError("request_record_corrupted","Generation request record is corrupted.",scene.scene_id) from error
            except GenerationRequestResolverError as error:
                raise ProjectVideoPreflightError("request_resolution_failed","Generation request could not be resolved.",scene.scene_id) from error
            try: VideoGenerationRequest.model_validate(request)
            except ValidationError as error:
                raise ProjectVideoPreflightError("request_validation_failed","Generation request validation failed.",scene.scene_id) from error
            readiness.append((scene.scene_id,request))
        try: configuration,provider=self._providers.construct(production.provider,self._environ)
        except KlingProviderConfigurationError as error:
            failure=ProjectVideoPreflightError("provider_configuration_invalid","Kling configuration is invalid.")
            failure.field_diagnostics=error.diagnostics; raise failure from error
        except KlingProviderRegistryError as error:
            raise ProjectVideoPreflightError("provider_unavailable","Configured video provider is unavailable.") from error
        ready=[]; mapping_failures=[]
        for scene_id,request in readiness:
            try: provider._mapper.map(request,external_task_id="local-preflight")
            except KlingUnsupportedConfigurationError as error:
                mapping_failures.append((scene_id,request.video_request.duration_seconds,error))
            else: ready.append(scene_id)
        if mapping_failures:
            durations=tuple((scene_id,request.video_request.duration_seconds) for scene_id,request in readiness)
            failure=ProjectVideoPreflightError("request_generation_settings_mismatch",
                "Video request is incompatible with configured Kling generation settings.",mapping_failures[0][0])
            unique={duration for _,duration in durations}
            if len(unique)==1:
                requested=durations[0][1]
                failure.field_diagnostics=(("KLING_DURATION",
                    f"configured {configuration.generation.duration}, request requires {requested}"),)
            else:
                values=", ".join(f"{scene_id}={duration}" for scene_id,duration in durations)
                failure.generation_diagnostics=(f"Scene durations are not uniform: {values}",)
                failure.field_diagnostics=(("KLING_DURATION",f"configured {configuration.generation.duration}"),)
            raise failure from mapping_failures[0][2]
        return project,production,tuple(ready)
