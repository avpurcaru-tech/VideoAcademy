import tempfile
import unittest
from pathlib import Path

from app.creative import DeterministicEpisodeGenerator, EducationalCreativeBrief, EpisodeGenerationService
from app.engines.director import DirectorEngine
from app.models import Camera, Character, Episode, Location, Metadata, Scene
from app.production import EpisodeProductionPlanner, GenerationRequestStore, SceneDurationPolicy
from app.prompts import PromptBuilder
from app.prompts.adapters import KlingPromptAdapter


def brief(duration=30, scene_count=3):
    return EducationalCreativeBrief(brief_id="duration-policy",topic="counting",learning_objectives=("count to two",),
        language="en",target_age_min=3,target_age_max=5,target_duration_seconds=duration,tone="cheerful",
        visual_style="simple animation",scene_count=scene_count,song_required=False)


class ArbitraryDurationGenerator:
    def generate_episode(self, value):
        character=Character(id="duration-policy-guide",name="Lumi",role="guide",description="Friendly guide",
                            appearance="Bright simple character")
        scenes=[Scene(number=index,narration="Learn",visual_description="A clear lesson",duration_seconds=7+index,
            character_ids=[character.id],location=Location(name="Garden",description="Safe garden",time_of_day="day"),
            camera=Camera(shot_type="wide",description="Stable view")) for index in range(1,value.scene_count+1)]
        return Episode(id=value.brief_id,title="Counting",lyrics="Original narration",
            metadata=Metadata(topic=value.topic,language=value.language,target_age_min=value.target_age_min,
                              target_age_max=value.target_age_max,tags=["educational"]),characters=[character],scenes=scenes)


class SceneDurationPlanningTests(unittest.TestCase):
    def test_thirty_second_target_creates_two_semantic_scenes(self):
        episode=EpisodeGenerationService(DeterministicEpisodeGenerator(),SceneDurationPolicy(15)).generate(brief())
        self.assertEqual([scene.number for scene in episode.scenes],[1,2])
        self.assertEqual([scene.duration_seconds for scene in episode.scenes],[15,15])

    def test_openai_like_semantic_durations_cannot_override_execution_duration(self):
        policy=SceneDurationPolicy(15)
        episode=EpisodeGenerationService(ArbitraryDurationGenerator(),policy).generate(brief())
        self.assertEqual([scene.duration_seconds for scene in episode.scenes],[8,9])
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); store=GenerationRequestStore(root/"requests")
            planner=EpisodeProductionPlanner(PromptBuilder(KlingPromptAdapter()),store,policy)
            kwargs=dict(production_id="duration-video",scene_output_directory=root/"scenes",workspace=root/"work",
                        destination=root/"master.mp4",provider="kling")
            director=DirectorEngine().create_plan(episode)
            preflight=planner.preflight(director,**kwargs)
            persisted=planner.plan(director,**kwargs)
            self.assertEqual(preflight,persisted)
            self.assertEqual([request.video_request.duration_seconds for request in persisted.video_requests],[15,15])
            self.assertEqual([request.video_request.scene_number for request in persisted.video_requests],[1,2])
            self.assertTrue(all(store.resolve(reference)==request for reference,request in zip(
                persisted.generation_request_references,persisted.video_requests,strict=True)))

    def test_nearest_scene_count_is_explicit_and_bounded(self):
        policy=SceneDurationPolicy(15)
        self.assertEqual(policy.scene_count(31),2)
        self.assertEqual(policy.scene_count(38),3)
        with self.assertRaisesRegex(RuntimeError,"scene-count bounds"):
            policy.scene_count(15)


if __name__=="__main__": unittest.main()
