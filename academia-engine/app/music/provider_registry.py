from dataclasses import dataclass
from typing import Callable

from .downloader import AtomicAudioArtifactDownloader
from .provider import MusicProvider


class MusicProviderRegistryError(RuntimeError): pass
class MusicProviderConfigurationError(MusicProviderRegistryError): pass


@dataclass(frozen=True)
class MusicProviderRuntime:
    provider: MusicProvider
    downloader: AtomicAudioArtifactDownloader


class MusicProviderRegistry:
    """Lazy registry: resolving a name is the first point credentials are read."""
    def __init__(self,factories: dict[str,Callable[[],MusicProviderRuntime]]|None=None):
        self._factories=factories or {"sunoapi_org":self._sunoapi_org,"mureka":self._mureka}

    def resolve(self,name: str) -> MusicProviderRuntime:
        factory=self._factories.get(name)
        if factory is None: raise MusicProviderRegistryError("Music provider is unsupported.")
        return factory()

    @staticmethod
    def _mureka():
        from app.providers.mureka_music_provider import MurekaMusicConfigurationError,MurekaMusicProvider
        try: provider=MurekaMusicProvider.from_environment()
        except MurekaMusicConfigurationError as error: raise MusicProviderConfigurationError("Legacy music provider configuration is missing.") from error
        return MusicProviderRuntime(provider,AtomicAudioArtifactDownloader(provider.download_audio_bytes))

    @staticmethod
    def _sunoapi_org():
        from app.providers.sunoapi_org_music_provider import SunoApiOrgConfigurationError,SunoApiOrgMusicProvider
        try: provider=SunoApiOrgMusicProvider.from_environment()
        except SunoApiOrgConfigurationError as error: raise MusicProviderConfigurationError("Third-party music provider configuration is missing.") from error
        return MusicProviderRuntime(provider,AtomicAudioArtifactDownloader(provider.download_audio_bytes))
