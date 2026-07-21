from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from app.production import (EpisodeProductionPlanner, GenerationRequestStore,
    SceneDurationPolicy, StoryboardVideoPlanner)
from app.prompts import PromptBuilder
from app.storyboard import DeterministicStoryboardGenerator

from tests.test_creative_storyboard import brief


class StoryboardVideoPlannerTests(unittest.TestCase):
    def setUp(self):
        self.storyboard = DeterministicStoryboardGenerator().generate_storyboard(brief())

    def test_storyboard_projects_only_visual_video_semantics(self):
        requests = StoryboardVideoPlanner().build(self.storyboard, "storyboard-video")
        self.assertEqual(len(requests), 3)
        first = requests[0].video_request; section = self.storyboard.sections[0]
        self.assertEqual(first.scene_number, 1)
        self.assertIn(section.environment, first.environment.location_description)
        self.assertIn(section.visual_goal, first.environment.location_description)
        self.assertIn("Objects:", first.environment.location_description)
        self.assertEqual(first.camera.description, section.camera_direction)
        self.assertEqual([character.name for character in first.characters], list(section.characters))
        self.assertNotIn(section.lyrics, first.environment.location_description)

    def test_scene_order_and_request_ids_are_deterministic(self):
        planner = StoryboardVideoPlanner()
        first = planner.build(self.storyboard, "storyboard-video")
        second = planner.build(self.storyboard, "storyboard-video")
        self.assertEqual(first, second)
        self.assertEqual([request.video_request.scene_number for request in first], [1, 2, 3])
        self.assertEqual([request.request_id for request in first], [
            "storyboard-video-scene-0001", "storyboard-video-scene-0002", "storyboard-video-scene-0003"])

    def test_existing_duration_policy_is_applied(self):
        requests = StoryboardVideoPlanner(SceneDurationPolicy(5)).build(self.storyboard, "duration-video")
        self.assertEqual([request.video_request.duration_seconds for request in requests], [5, 5, 5])
        self.assertEqual([section.estimated_duration_seconds for section in self.storyboard.sections], [10, 10, 10])

    def test_episode_production_planner_accepts_storyboard_without_prompt_builder_or_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); prompt_builder=Mock(spec=PromptBuilder)
            planner=EpisodeProductionPlanner(prompt_builder,GenerationRequestStore(root/"requests"),SceneDurationPolicy(5))
            with patch("app.providers.kling_provider.KlingProvider") as provider:
                planned=planner.plan(self.storyboard,"storyboard-video",root/"scenes",root/"workspace",root/"final.mp4")
            self.assertEqual([value.video_request.duration_seconds for value in planned.video_requests],[5,5,5])
            self.assertEqual(planned.source_scene_ids,tuple(section.section_id for section in self.storyboard.sections))
            prompt_builder.build.assert_not_called(); provider.assert_not_called()

    def test_legacy_director_plan_pipeline_remains_available(self):
        # Existing planner behavior is covered comprehensively by test_episode_production_planner;
        # this assertion ensures the public legacy entry point remains present.
        self.assertTrue(callable(EpisodeProductionPlanner.preflight))


if __name__=="__main__": unittest.main()
