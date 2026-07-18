from app.models import DirectorPlan, VideoRequest

from .contracts import VideoPromptAdapter


class PromptBuilder:
    def __init__(self, adapter: VideoPromptAdapter) -> None:
        self._adapter = adapter

    def build(self, director_plan: DirectorPlan) -> list[VideoRequest]:
        return [
            self._adapter.create_video_request(scene)
            for scene in director_plan.scenes
        ]
