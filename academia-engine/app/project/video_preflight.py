import os

from pydantic import ValidationError

from app.models import VideoGenerationRequest
from app.production import (GenerationRequestCorruptedError,GenerationRequestNotFoundError,
                            GenerationRequestResolverError,GenerationRequestStore,ProductionRegistry)


class ProjectVideoPreflightError(RuntimeError):
    def __init__(self,category,safe_message,scene_id=None):
        super().__init__(safe_message); self.category=category; self.scene_id=scene_id


class ProjectVideoPreflightService:
    def __init__(self,project_registry,production_registry=None,request_store=None,environ=None):
        self._projects=project_registry
        self._productions=production_registry or ProductionRegistry()
        self._requests=request_store or GenerationRequestStore()
        self._environ=os.environ if environ is None else environ

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
            readiness.append(scene.scene_id)
        if production.provider!="kling":
            raise ProjectVideoPreflightError("provider_unavailable","Configured video provider is unavailable.")
        key=self._environ.get("KLING_API_KEY")
        if not isinstance(key,str) or not key.strip():
            raise ProjectVideoPreflightError("provider_configuration_missing","Kling provider configuration is missing.")
        return project,production,tuple(readiness)
