from app.models import Camera, Transition, VideoEnvironment, VideoGenerationRequest, VideoRequest


def build_smoke_test_request() -> VideoGenerationRequest:
    """Return the existing provider-neutral smoke-test request used by diagnostic CLIs."""
    return VideoGenerationRequest(
        request_id="kling-smoke-test",
        video_request=VideoRequest(
            scene_number=1,
            duration_seconds=15,
            environment=VideoEnvironment(
                location_name="sunny garden",
                location_description="A small cheerful garden with flowers and a friendly ladybug.",
                time_of_day="morning",
                lighting_description="soft warm daylight",
                lighting_intensity="medium",
            ),
            camera=Camera(shot_type="wide", description="A calm, stable opening view."),
            transition=Transition(type="fade"),
        ),
    )
