class MusicTimelineGeneratorRegistry:
    def resolve(self, name="openai"):
        if name != "openai": raise ValueError("Music timeline generator is unsupported.")
        from app.providers.openai_music_timeline_provider import OpenAIMusicTimelineGenerator
        return OpenAIMusicTimelineGenerator()
