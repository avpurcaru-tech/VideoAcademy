class EpisodeGeneratorRegistryError(RuntimeError): pass
class UnknownEpisodeGeneratorError(EpisodeGeneratorRegistryError): pass


class EpisodeGeneratorRegistry:
    def __init__(self,factories=None):
        self._factories=factories or {"deterministic":self._deterministic,"openai":self._openai}
    def resolve(self,name):
        factory=self._factories.get(name)
        if factory is None: raise UnknownEpisodeGeneratorError("Episode generator is unsupported.")
        return factory()
    @staticmethod
    def _deterministic():
        from .episode_generator import DeterministicEpisodeGenerator
        return DeterministicEpisodeGenerator()
    @staticmethod
    def _openai():
        from app.providers.openai_episode_provider import OpenAIEpisodeGenerator
        return OpenAIEpisodeGenerator()
