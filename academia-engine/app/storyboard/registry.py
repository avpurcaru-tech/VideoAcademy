class StoryboardGeneratorRegistryError(RuntimeError): pass
class UnsupportedStoryboardGeneratorError(StoryboardGeneratorRegistryError): pass


class StoryboardGeneratorRegistry:
    def resolve(self, name):
        if name == "deterministic":
            from .generator import DeterministicStoryboardGenerator
            return DeterministicStoryboardGenerator()
        if name == "openai":
            from app.providers.openai_storyboard_provider import OpenAIStoryboardGenerator
            return OpenAIStoryboardGenerator()
        raise UnsupportedStoryboardGeneratorError("Storyboard generator is unsupported.")
