from typing import Mapping

from app.config import KlingProviderConfiguration

from .kling_client import KlingHttpClient
from .kling_mapper import KlingTextToVideoMapper
from .kling_provider import KlingProvider


class KlingProviderRegistryError(RuntimeError): pass


class KlingProviderRegistry:
    """Single local construction path; resolving never performs HTTP."""
    def construct(self,provider_name="kling",environment: Mapping[str,str]|None=None):
        if provider_name!="kling": raise KlingProviderRegistryError("Video provider is unsupported.")
        configuration=KlingProviderConfiguration.from_environment(environment).validate_configuration()
        client=KlingHttpClient(api_key=configuration.api_key.get_secret_value(),base_url=configuration.base_url)
        mapper=KlingTextToVideoMapper(configuration.generation)
        return configuration,KlingProvider(client=client,mapper=mapper,generation_settings=configuration.generation)
