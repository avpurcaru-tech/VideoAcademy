from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlparse

from app.config import KlingProviderConfiguration

from .kling_client import KlingHttpClient
from .kling_mapper import KlingTextToVideoMapper
from .kling_provider import KlingProvider
from .kling_image_to_video import KlingImageToVideoProvider
from app.visual_references import VisualReferencePublicationRegistry


class KlingProviderRegistryError(RuntimeError): pass
class KlingProviderCredentialsMissingError(KlingProviderRegistryError): pass
class KlingReferencePublisherUnavailableError(KlingProviderRegistryError): pass

@dataclass(frozen=True)
class KlingProviderRuntime:
    provider_key: str
    provider: object
    publication_registry: VisualReferencePublicationRegistry|None=None
    task_registry: object|None=None
    downloader: object|None=None


class KlingProviderRegistry:
    """Single local construction path; resolving never performs HTTP."""
    @staticmethod
    def capabilities(provider_name="kling",environment=None):
        source=environment or {}
        raw=source.get("VIDEO_COST_PER_GENERATED_SECOND")
        cost=float(raw) if raw not in (None,"") else None
        if provider_name=="kling": return KlingProvider.capability_snapshot(cost)
        if provider_name=="kling_image_to_video": return KlingImageToVideoProvider.capability_snapshot(cost)
        raise KlingProviderRegistryError("Video provider is unsupported.")
    @staticmethod
    def request_mapper(provider_name,publication_registry=None,generation_settings=None):
        if provider_name=="kling_image_to_video":
            from .kling_image_to_video import KlingImageToVideoMapper
            return KlingImageToVideoMapper(publication_registry or VisualReferencePublicationRegistry())
        if provider_name=="kling":
            return KlingTextToVideoMapper(generation_settings or KlingProviderConfiguration.from_environment().generation)
        raise KlingProviderRegistryError("Video provider is unsupported.")
    def construct_runtime(self,provider_name="kling",environment: Mapping[str,str]|None=None,
                          publication_registry=None,visual_reference_publisher=None,task_registry=None,downloader=None,
                          scene_first_frame_workflow=None,scene_first_frame_generator=None,scene_first_frame_store=None):
        if provider_name not in ("kling","kling_image_to_video"): raise KlingProviderRegistryError("Video provider is unsupported.")
        try: configuration=KlingProviderConfiguration.from_environment(environment).validate_configuration()
        except Exception as error:
            diagnostics=getattr(error,"diagnostics",())
            if ("KLING_API_KEY","missing") in diagnostics: raise KlingProviderCredentialsMissingError("Kling credentials are missing.") from error
            raise
        client=KlingHttpClient(api_key=configuration.api_key.get_secret_value(),base_url=configuration.base_url)
        if task_registry is None:
            from app.services import TaskRegistry
            task_registry=TaskRegistry()
        if downloader is None:
            from .kling_downloader import KlingVideoArtifactDownloader
            downloader=KlingVideoArtifactDownloader()
        mapper=KlingTextToVideoMapper(configuration.generation)
        if provider_name=="kling_image_to_video":
            publications=publication_registry or VisualReferencePublicationRegistry()
            if scene_first_frame_workflow is None and scene_first_frame_generator is not None:
                from app.scene_first_frames import SceneFirstFrameWorkflow
                scene_first_frame_workflow=SceneFirstFrameWorkflow(scene_first_frame_generator,scene_first_frame_store,
                    publications,visual_reference_publisher)
            callback=(environment or {}).get("KLING_CALLBACK_URL") if environment is not None else None
            if callback:
                parsed=urlparse(callback)
                if parsed.scheme!="https" or not parsed.netloc: raise KlingProviderRegistryError("Kling callback URL is invalid.")
            provider=KlingImageToVideoProvider(client=client,mapper=self.request_mapper(provider_name,publications),
                callback_url=callback,first_frame_workflow=scene_first_frame_workflow)
            return configuration,KlingProviderRuntime(provider_name,provider,publications,task_registry,downloader)
        return configuration,KlingProviderRuntime(provider_name,
            KlingProvider(client=client,mapper=mapper,generation_settings=configuration.generation),None,task_registry,downloader)

    def construct(self,provider_name="kling",environment: Mapping[str,str]|None=None,**dependencies):
        try: configuration,runtime=self.construct_runtime(provider_name,environment,**dependencies)
        except KlingProviderCredentialsMissingError as error:
            if error.__cause__ is not None: raise error.__cause__
            raise
        return configuration,runtime.provider
