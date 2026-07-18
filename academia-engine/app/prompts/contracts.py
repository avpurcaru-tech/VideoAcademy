from typing import Protocol

from app.models import DirectorScene, VideoRequest


class VideoPromptAdapter(Protocol):
    def create_video_request(self, scene: DirectorScene) -> VideoRequest:
        """Convert one provider-neutral director scene to a video request."""
