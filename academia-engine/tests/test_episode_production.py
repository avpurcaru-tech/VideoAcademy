import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.media import MediaProbeResult
from app.models import Camera, GenerationTaskStatus, Transition, VideoEnvironment, VideoGenerationRequest, VideoRequest
from app.production import (EpisodeProductionConflictError, EpisodeProductionOrchestrator,
                            EpisodeProductionRequest, EpisodeProductionStatus, EpisodeSceneArtifactMissingError,
                            EpisodeTransitionPolicy, ProductionRecord, ProductionRegistry,
                            ProductionRegistryError)
from app.services import ArtifactRecord, GenerationTaskRecord, VideoPollingPolicy
from app.timeline import RenderedTimelineArtifact


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
POLICY = VideoPollingPolicy(interval_seconds=1, timeout_seconds=10)


class EpisodeProductionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.registry = ProductionRegistry(self.root / "productions")
        self.engine = FakeEngine()
        self.renderer = FakeRenderer(self.root / "final.mp4")
        self.orchestrator = EpisodeProductionOrchestrator(self.engine, self.renderer, self.registry, FakeProbe(), clock=lambda: NOW)

    def tearDown(self): self.temp.cleanup()

    def test_happy_path_is_ordered_deterministic_prompt_free_and_rendered(self):
        request = self.request("fade", .5)
        result = self.orchestrator.produce(request, POLICY)
        self.assertEqual(result.status, EpisodeProductionStatus.SUCCEEDED)
        self.assertEqual([scene.scene_id for scene in result.scenes], ["scene-0001", "scene-0002"])
        self.assertEqual(self.engine.destinations, [self.root / "scenes/scene-0001.mp4", self.root / "scenes/scene-0002.mp4"])
        timeline = self.renderer.plan
        self.assertEqual(timeline.transitions[0].kind.value, "fade")
        self.assertEqual(timeline.transitions[0].duration_seconds, .5)
        self.assertEqual(len(timeline.transitions), 1)
        manifest = (self.root / "productions/episode-001.json").read_text()
        self.assertNotIn("location_description", manifest)
        self.assertNotIn("prompt", manifest.lower())
        self.assertNotIn("signed", manifest.lower())
        self.assertEqual(json.loads(manifest)["status"], "succeeded")

    def test_cut_policy_and_duplicate_are_enforced(self):
        request = self.request("cut", None)
        self.orchestrator.produce(request, POLICY)
        self.assertEqual(self.renderer.plan.transitions, ())
        with self.assertRaises(EpisodeProductionConflictError): self.orchestrator.produce(request, POLICY)

    def test_resume_reuses_downloaded_scene_without_provider(self):
        result = self.orchestrator.produce(self.request("cut", None), POLICY)
        calls = len(self.engine.resume_calls)
        resumed = self.orchestrator.resume("episode-001", POLICY)
        self.assertEqual(resumed, result); self.assertEqual(len(self.engine.resume_calls), calls)

    def test_missing_durable_artifact_is_explicit_and_marks_failed(self):
        request = self.request("cut", None)
        self.orchestrator._requests[request.production_id] = request.video_requests
        self.orchestrator._request_contracts[request.production_id] = request
        missing = self.root / "missing.mp4"
        scenes = ({"scene_id":"scene-0001","order":0,"provider_task_id":"task-1","normalized_status":"succeeded","local_path":missing,"artifact_id":"a","sha256":"a"*64},
                  {"scene_id":"scene-0002","order":1,"provider_task_id":"task-2","normalized_status":"succeeded","local_path":missing,"artifact_id":"b","sha256":"b"*64})
        self.registry.create(ProductionRecord(production_id="episode-001", status="failed", provider="fake", scenes=scenes, created_at=NOW, updated_at=NOW))
        with self.assertRaises(EpisodeSceneArtifactMissingError): self.orchestrator.resume("episode-001", POLICY)
        self.assertEqual(self.registry.load("episode-001").status, EpisodeProductionStatus.FAILED)

    def test_registry_rejects_path_traversal_and_serialization_is_deterministic(self):
        with self.assertRaises(ProductionRegistryError): self.registry.exists("../escape")
        request = self.request("cut", None)
        self.assertEqual(request.to_json(), EpisodeProductionRequest.from_json(request.to_json()).to_json())

    def request(self, kind, duration):
        return EpisodeProductionRequest(production_id="episode-001", provider="fake",
            video_requests=(generation("request-1", 1), generation("request-2", 2)),
            scene_output_directory=self.root / "scenes", final_output_path=self.root / "final.mp4",
            media_workspace=self.root / "media", transition_policy=EpisodeTransitionPolicy(kind=kind, duration_seconds=duration))


def generation(request_id, number):
    return VideoGenerationRequest(request_id=request_id, video_request=VideoRequest(scene_number=number, duration_seconds=5,
        environment=VideoEnvironment(location_name="room", location_description="secret semantic prompt", time_of_day="day", lighting_description="light", lighting_intensity="medium"),
        camera=Camera(shot_type="wide", description="wide"), transition=Transition(type="cut")))


class FakeEngine:
    def __init__(self): self.submits=[]; self.resume_calls=[]; self.destinations=[]
    def submit(self, request, provider=None):
        self.submits.append(request.request_id); task=f"task-{len(self.submits)}"
        return GenerationTaskRecord(provider=provider, provider_task_id=task, normalized_status="submitted", created_at=NOW, updated_at=NOW)
    def resume(self, task, destination, policy):
        self.resume_calls.append(task); self.destinations.append(destination); destination.parent.mkdir(parents=True, exist_ok=True); destination.write_bytes(b"mp4")
        artifact=ArtifactRecord(artifact_id=f"artifact-{task}", local_path=destination, byte_size=3, sha256="a"*64, content_type="video/mp4")
        return GenerationTaskRecord(provider="fake", provider_task_id=task, normalized_status="succeeded", created_at=NOW, updated_at=NOW, artifact=artifact)


class FakeProbe:
    def probe_video(self, path): return MediaProbeResult(local_path=path, duration_seconds=5, width=1280, height=720, frame_rate=30, video_codec="h264", has_audio=False, container_format="mp4")


class FakeRenderer:
    def __init__(self, path): self.path=path; self.plan=None
    def render(self, plan):
        self.plan=plan
        media=MediaProbeResult(local_path=self.path, duration_seconds=plan.expected_duration_seconds, width=1280, height=720, frame_rate=30, video_codec="h264", has_audio=False, container_format="mp4")
        return RenderedTimelineArtifact(timeline_id=plan.timeline_id, local_path=self.path, byte_size=10, sha256="f"*64, media_info=media, source_count=2, transition_count=len(plan.transitions))


if __name__ == "__main__": unittest.main()
