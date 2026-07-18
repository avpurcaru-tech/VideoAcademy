import unittest

from app.engines.video import VideoEngine
from app.models import (
    Camera,
    Transition,
    VideoEnvironment,
    VideoGenerationRequest,
    VideoGenerationResult,
    VideoRequest,
)
from app.providers import KlingProvider


class VideoProviderTests(unittest.TestCase):
    def test_kling_provider_does_not_generate_scenes_yet(self) -> None:
        with self.assertRaises(NotImplementedError):
            KlingProvider(client=FakeKlingClient()).generate_scene(self._generation_request())

    def test_video_engine_uses_the_injected_provider(self) -> None:
        provider = FakeVideoProvider()

        result = VideoEngine(provider).generate_scene(self._generation_request())

        self.assertTrue(provider.was_called)
        self.assertEqual(result.provider_name, "fake")

    @staticmethod
    def _generation_request() -> VideoGenerationRequest:
        return VideoGenerationRequest(
            request_id="scene-01",
            video_request=VideoRequest(
                scene_number=1,
                duration_seconds=30,
                environment=VideoEnvironment(
                    location_name="Spațiu",
                    location_description="Cer plin de stele.",
                    time_of_day="noapte",
                    lighting_description="Lumină blândă de lună.",
                    lighting_intensity="medium",
                ),
                camera=Camera(shot_type="wide", description="Cadru larg."),
                transition=Transition(type="fade_to_black"),
            ),
        )


class FakeVideoProvider:
    def __init__(self) -> None:
        self.was_called = False

    def generate_scene(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        self.was_called = True
        return VideoGenerationResult(
            request_id=request.request_id,
            scene_number=request.video_request.scene_number,
            provider_name="fake",
            status="mocked",
            is_mock=True,
        )


class FakeKlingClient:
    def get_account_usage(self) -> dict[str, object]:
        return {"status": "ok"}
