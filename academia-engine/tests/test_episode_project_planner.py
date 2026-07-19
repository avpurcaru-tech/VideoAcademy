import tempfile
import unittest
from pathlib import Path

from app.cli.episode_project_plan import load_episode
from app.engines.director import DirectorEngine
from app.production import (EpisodeProjectPlanner, EpisodeProjectGenerationService,
                            EpisodeTransitionPolicy, GenerationRequestStore)
from app.production.planner import EpisodeProductionPlanner
from app.prompts import PromptBuilder
from app.prompts.adapters import KlingPromptAdapter


ROOT=Path(__file__).resolve().parents[1]; FIXTURE=ROOT/"examples"/"smoke"/"episode-input.json"


class EpisodeProjectPlannerTests(unittest.TestCase):
    def test_fixture_directs_and_preflights_deterministically_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); episode=load_episode(FIXTURE); store=GenerationRequestStore(root/"requests")
            project=EpisodeProjectPlanner(DirectorEngine(),EpisodeProductionPlanner(PromptBuilder(KlingPromptAdapter()),store))
            request=project.preflight(episode,"project-001",root/"scenes",root/"media",root/"final.mp4",provider="fake",
                                      transition=EpisodeTransitionPolicy(kind="fade",duration_seconds=0.5))
            self.assertEqual([item.video_request.scene_number for item in request.video_requests],[1,2])
            self.assertEqual([item.reference_id for item in request.generation_request_references],["project-001-scene-0001","project-001-scene-0002"])
            self.assertEqual(request.source_scene_ids,("garden-counting-001-scene-0001","garden-counting-001-scene-0002"))
            self.assertFalse((root/"requests").exists())

    def test_normal_project_planning_persists_and_facade_delegates_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); episode=load_episode(FIXTURE); store=GenerationRequestStore(root/"requests")
            production=EpisodeProductionPlanner(PromptBuilder(KlingPromptAdapter()),store); project=EpisodeProjectPlanner(DirectorEngine(),production)
            config=dict(production_id="project-001",scene_output_directory=root/"scenes",workspace=root/"media",destination=root/"final.mp4",provider="fake")
            request=project.plan_episode(episode,**config)
            self.assertEqual(store.resolve(request.generation_request_references[0]),request.video_requests[0])
            orchestrator=type("Fake",(),{})(); orchestrator.calls=[]; orchestrator.produce=lambda request,policy: orchestrator.calls.append((request,policy)) or "done"
            second_store=GenerationRequestStore(root/"requests-2"); service=EpisodeProjectGenerationService(
                EpisodeProjectPlanner(DirectorEngine(),EpisodeProductionPlanner(PromptBuilder(KlingPromptAdapter()),second_store)),orchestrator)
            self.assertEqual(service.plan_and_produce(episode,"policy",production_id="project-002",**{k:v for k,v in config.items() if k!="production_id"}),"done")
            self.assertEqual(len(orchestrator.calls),1)


if __name__=="__main__": unittest.main()
