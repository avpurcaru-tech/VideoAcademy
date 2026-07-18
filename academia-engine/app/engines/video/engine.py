from app.models import VideoGenerationRequest, VideoGenerationResult
from app.providers.video_provider import VideoProvider


class VideoEngine:
    def __init__(self, provider: VideoProvider) -> None:
        self._provider = provider

    def generate_scene(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        return self._provider.generate_scene(request)
