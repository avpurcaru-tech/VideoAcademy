import tempfile
import unittest
from pathlib import Path

from app.models import (Camera, DirectorPlan, DirectorScene, Lighting, Location, Transition,
                        VideoEnvironment, VideoRequest)
from app.production import (EpisodeGenerationService, EpisodeProductionPlanner,
                            EpisodeProductionRequestConflictError, EpisodeProductionSceneOrderError,
                            EpisodeSceneResult, EpisodeSceneStatus, EpisodeTransitionPolicy,
                            GenerationRequestReference, GenerationRequestStore)
from app.prompts import PromptBuilder


class EpisodeProductionPlannerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        self.adapter=RecordingAdapter(); self.store=GenerationRequestStore(self.root/"requests")
        self.planner=EpisodeProductionPlanner(PromptBuilder(self.adapter),self.store)
    def tearDown(self): self.temp.cleanup()

    def test_director_order_builds_deterministic_requests_references_and_traceability(self):
        request=self.plan(plan((2,1)),"fade",0.5)
        self.assertEqual(self.adapter.calls,[1,2])
        self.assertEqual([item.video_request.scene_number for item in request.video_requests],[1,2])
        self.assertEqual([str(item) for item in request.generation_request_references],["episode-001-scene-0001","episode-001-scene-0002"])
        self.assertEqual(request.source_scene_ids,("story-001-scene-0001","story-001-scene-0002"))
        for reference, expected in zip(request.generation_request_references,request.video_requests):
            self.assertEqual(self.store.resolve(reference),expected)

    def test_cut_fade_and_dissolve_use_semantic_transition_policy(self):
        for kind,duration in (("cut",None),("fade",0.5),("dissolve",0.75)):
            with self.subTest(kind=kind):
                request=self.plan(plan((1,2)),kind,duration,production_id=f"episode-{kind}")
                self.assertEqual(request.transition_policy.kind.value,kind); self.assertEqual(request.transition_policy.duration_seconds,duration)

    def test_duplicate_or_noncontiguous_scene_order_is_rejected_before_store(self):
        for numbers in ((1,1),(1,3)):
            with self.subTest(numbers=numbers),self.assertRaises(EpisodeProductionSceneOrderError): self.plan(plan(numbers),"cut",None)
        self.assertEqual(list((self.root/"requests").glob("*.json")) if (self.root/"requests").exists() else [],[])

    def test_identical_reference_is_idempotent_and_conflicting_reference_rejected(self):
        director=plan((1,2)); first=self.plan(director,"cut",None)
        self.assertEqual(self.plan(director,"cut",None),first)
        changed=plan((1,2)); changed.scenes[0].camera.description="A meaningfully different view"
        with self.assertRaises(EpisodeProductionRequestConflictError): self.plan(changed,"cut",None)

    def test_facade_delegates_production_without_duplicating_workflow(self):
        orchestrator=type("Orchestrator",(),{})(); orchestrator.calls=[]
        orchestrator.produce=lambda request,policy: orchestrator.calls.append((request,policy)) or "result"
        service=EpisodeGenerationService(self.planner,orchestrator)
        self.assertEqual(service.plan_and_produce(plan((1,2)),"policy",production_id="facade-001",**self.configuration()),"result")
        self.assertEqual(len(orchestrator.calls),1)

    def test_legacy_and_local_scene_status_are_ready_without_provider_status(self):
        scene=EpisodeSceneResult.model_validate({"scene_id":"scene-0001","order":0,
            "generation_request_reference":{"reference_id":"ref-1"},"local_path":"scene.mp4","artifact_id":"local:a","sha256":"a"*64})
        self.assertEqual(scene.production_status,EpisodeSceneStatus.READY); self.assertIsNone(scene.normalized_status)

    def plan(self,director,kind,duration,production_id="episode-001"):
        return self.planner.plan(director,transition=EpisodeTransitionPolicy(kind=kind,duration_seconds=duration),
                                 production_id=production_id,**self.configuration())
    def configuration(self):
        return dict(scene_output_directory=self.root/"scenes",workspace=self.root/"media",destination=self.root/"final.mp4",provider="fake")


class RecordingAdapter:
    def __init__(self): self.calls=[]
    def create_video_request(self,scene):
        self.calls.append(scene.scene_number)
        return VideoRequest(scene_number=scene.scene_number,duration_seconds=scene.duration_seconds,
            environment=VideoEnvironment(location_name=scene.location.name,location_description=scene.location.description,
                time_of_day=scene.location.time_of_day,lighting_description=scene.lighting.description,lighting_intensity=scene.lighting.intensity),
            camera=scene.camera,transition=scene.transition)


def plan(numbers):
    return DirectorPlan(episode_id="story-001",episode_title="Safe title",scenes=[DirectorScene(scene_number=number,duration_seconds=15,
        location=Location(name=f"Location {number}",description="Semantic scene description",time_of_day="day"),
        camera=Camera(shot_type="wide",description="Stable view"),lighting=Lighting(description="Soft light"),
        transition=Transition(type="cut")) for number in numbers])


if __name__=="__main__": unittest.main()
