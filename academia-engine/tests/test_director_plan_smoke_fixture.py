import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.cli.episode_plan import load_semantic_input, print_plan
from app.models import DirectorPlan
from app.production import EpisodeProductionPlanner, EpisodeTransitionPolicy, GenerationRequestStore
from app.prompts import PromptBuilder
from app.prompts.adapters import KlingPromptAdapter


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "examples" / "smoke" / "director-plan.json"


class DirectorPlanSmokeFixtureTests(unittest.TestCase):
    def test_fixture_loads_through_episode_plan_and_matches_director_contract(self):
        loaded = load_semantic_input(FIXTURE)
        self.assertEqual(loaded.input_type, "DirectorPlan")
        self.assertIsInstance(loaded.director_plan, DirectorPlan)
        self.assertEqual([scene.scene_number for scene in loaded.director_plan.scenes], [1, 2])
        self.assertNotEqual(loaded.director_plan.scenes[0].location.name, loaded.director_plan.scenes[1].location.name)

    def test_fixture_passes_non_mutating_planner_preflight_with_deterministic_traceability(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = GenerationRequestStore(root / "requests")
            planner = EpisodeProductionPlanner(PromptBuilder(KlingPromptAdapter()), store)
            request = planner.preflight(
                load_semantic_input(FIXTURE).director_plan,
                "director-smoke-001",
                root / "scenes",
                root / "media",
                root / "final.mp4",
                provider="kling",
                transition=EpisodeTransitionPolicy(kind="fade", duration_seconds=0.5),
            )
            self.assertEqual(
                [reference.reference_id for reference in request.generation_request_references],
                ["director-smoke-001-scene-0001", "director-smoke-001-scene-0002"],
            )
            self.assertEqual(request.source_scene_ids, ("garden-colors-001-scene-0001", "garden-colors-001-scene-0002"))
            self.assertFalse((root / "requests").exists())

    def test_fixture_and_planning_output_contain_no_provider_secrets_or_semantic_bodies(self):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        serialized = json.dumps(payload).lower()
        for forbidden in ("authorization", "api_key", "credential", "signed_url", "callback_url", "provider_payload", "http://", "https://"):
            self.assertNotIn(forbidden, serialized)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            planner = EpisodeProductionPlanner(PromptBuilder(KlingPromptAdapter()), GenerationRequestStore(root / "requests"))
            request = planner.preflight(load_semantic_input(FIXTURE).director_plan, "director-smoke-001", root/"scenes", root/"media", root/"final.mp4")
            output = []
            print_plan(request, input_type="DirectorPlan", preflight=True, emit=output.append)
            text = "\n".join(output)
            self.assertNotIn("cheerful preschool garden", text)
            self.assertNotIn("Lila walks", text)
            self.assertNotIn("request_id", text)


if __name__ == "__main__":
    unittest.main()
