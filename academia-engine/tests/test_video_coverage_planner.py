import unittest

from app.storyboard import DeterministicStoryboardGenerator
from app.creative import EducationalCreativeBrief
from app.video_coverage import *


def storyboard(sections=1,duration=32):
    brief=EducationalCreativeBrief(brief_id=f"generic-{sections}-{duration}",topic="generic",learning_objectives=("learn",),
        language="en",target_age_min=3,target_age_max=6,target_duration_seconds=duration,tone="warm",visual_style="simple",
        scene_count=max(2,sections),song_required=True)
    value=DeterministicStoryboardGenerator().generate_storyboard(brief)
    if sections==1:
        section=value.sections[0].model_copy(update={"estimated_duration_seconds":duration})
        value=value.model_copy(update={"sections":(section,),"target_duration_seconds":duration})
    return value

def capability(clip,cost=None):
    return VideoProviderCapabilities(provider_name="generic",supported_clip_durations=(clip,),selected_clip_duration=clip,
        supports_reference_images=True,cost_per_generated_second=cost)


class VideoCoveragePlannerTests(unittest.TestCase):
    def test_one_32_second_audio_with_5_second_full_generation(self):
        value=VideoCoveragePlanner().plan({"variant-01":32},storyboard(),capability(5),
            VideoCoverageConfiguration(policy="full_generation"))
        self.assertEqual(32,value.coverage_duration_seconds); self.assertEqual(7,value.unique_scene_count)
        self.assertEqual(0,value.derived_or_reused_scene_count); self.assertEqual(3,value.variant_plans[0].final_trim_seconds)

    def test_longest_variant_drives_shared_pool_and_shorter_uses_prefix(self):
        value=VideoCoveragePlanner().plan({"variant-01":73,"variant-02":91},storyboard(4,91),capability(10),
            VideoCoverageConfiguration(policy="full_generation"))
        self.assertEqual(91,value.coverage_duration_seconds); self.assertEqual(10,value.unique_scene_count)
        self.assertEqual(8,len(value.variant_plans[0].usages)); self.assertEqual(7,value.variant_plans[0].final_trim_seconds)
        self.assertEqual(value.shared_usage_plan[:8],value.variant_plans[0].usages)

    def test_balanced_reuses_deterministically(self):
        planner=VideoCoveragePlanner(); config=VideoCoverageConfiguration(policy="balanced",balanced_unique_coverage_ratio=.5)
        first=planner.plan({"a":91},storyboard(3,91),capability(10),config)
        second=planner.plan({"a":91},storyboard(3,91),capability(10),config)
        self.assertEqual(first,second); self.assertEqual(5,first.unique_scene_count); self.assertEqual(5,first.derived_or_reused_scene_count)
        self.assertTrue(any(value.reused for value in first.shared_usage_plan))

    def test_budget_cap_and_cost_guard(self):
        value=VideoCoveragePlanner().plan({"a":91},storyboard(3,91),capability(10,.5),
            VideoCoverageConfiguration(policy="budget",maximum_scene_count=4,maximum_generation_budget=20))
        self.assertEqual(4,value.unique_scene_count); self.assertEqual(20,value.estimated_provider_cost)
        self.assertFalse(value.confirmation_required)

    def test_cost_above_non_budget_limit_requires_confirmation(self):
        value=VideoCoveragePlanner().plan({"a":32},storyboard(2,32),capability(5,1),
            VideoCoverageConfiguration(policy="full_generation",maximum_generation_budget=20))
        self.assertEqual(35,value.estimated_provider_cost); self.assertTrue(value.confirmation_required)

    def test_longer_section_receives_more_unique_shots(self):
        value=storyboard(2,100)
        first=value.sections[0].model_copy(update={"estimated_duration_seconds":20})
        second=value.sections[1].model_copy(update={"estimated_duration_seconds":80})
        value=value.model_copy(update={"sections":(first,second)})
        plan=VideoCoveragePlanner().plan({"a":100},value,capability(10),VideoCoverageConfiguration(policy="full_generation"))
        counts={section.section_id:sum(shot.source_storyboard_section_id==section.section_id for shot in plan.unique_shots)
            for section in value.sections}
        self.assertGreater(counts[second.section_id],counts[first.section_id])

    def test_refrain_is_preferred_for_balanced_reuse(self):
        value=storyboard(2,100)
        refrain=value.sections[1].model_copy(update={"section_type":"refrain"})
        value=value.model_copy(update={"sections":(value.sections[0],refrain)})
        plan=VideoCoveragePlanner().plan({"a":100},value,capability(10),
            VideoCoverageConfiguration(policy="balanced",balanced_unique_coverage_ratio=.5))
        reused=[usage for usage in plan.shared_usage_plan if usage.reused]
        self.assertTrue(reused); self.assertTrue(all(value.source_storyboard_section_id==refrain.section_id for value in reused))

    def test_expanded_shots_preserve_source_and_are_unique(self):
        value=storyboard(2,73); plan=VideoCoveragePlanner().plan({"a":73},value,capability(5),
            VideoCoverageConfiguration(policy="full_generation"))
        from app.production import StoryboardVideoPlanner,SceneDurationPolicy
        requests=StoryboardVideoPlanner(SceneDurationPolicy(5)).build(value,"generic-video",plan)
        self.assertEqual(plan.unique_scene_count,len(requests))
        self.assertEqual(len(requests),len({request.request_id for request in requests}))
        self.assertTrue(all(request.video_request.duration_seconds==5 for request in requests))

    def test_production_plan_persists_reuse_without_duplicate_generation_identity(self):
        value=storyboard(2,73); plan=VideoCoveragePlanner().plan({"a":73},value,capability(5),
            VideoCoverageConfiguration(policy="balanced",balanced_unique_coverage_ratio=.5))
        from app.production import EpisodeProductionPlanner,GenerationRequestStore,SceneDurationPolicy
        from unittest.mock import Mock
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as root:
            request=EpisodeProductionPlanner(Mock(),GenerationRequestStore(Path(root)/"requests"),SceneDurationPolicy(5)).preflight(
                value,"generic-video",Path(root)/"scenes",Path(root)/"work",Path(root)/"master.mp4",
                provider="generic",coverage_plan=plan)
        self.assertEqual(len(plan.shared_usage_plan),len(request.video_requests))
        self.assertEqual(plan.derived_or_reused_scene_count,sum(value is not None for value in request.reuse_source_indices))
        self.assertEqual(len(request.video_requests),len({value.request_id for value in request.video_requests}))

    def test_schedule_validation_reserves_unique_outro_and_alternates_refrain(self):
        value=storyboard(3,120)
        sections=(value.sections[0].model_copy(update={"section_type":"intro"}),
            value.sections[1].model_copy(update={"section_type":"refrain"}),
            value.sections[2].model_copy(update={"section_type":"outro"}))
        value=value.model_copy(update={"sections":sections})
        plan=VideoCoveragePlanner().plan({"long":116,"short":98},value,capability(10),
            VideoCoverageConfiguration(policy="balanced",balanced_unique_coverage_ratio=.65))
        references={shot.shot_id:"a"*64 for shot in plan.unique_shots}
        self.assertEqual(12,len(plan.shared_usage_plan))
        self.assertFalse(plan.shared_usage_plan[-1].reused)
        self.assertEqual(sections[-1].section_id,plan.shared_usage_plan[-1].source_storyboard_section_id)
        self.assertEqual((),VideoCoveragePlanValidator().validate(plan,value,references))
        self.assertTrue(all(a.shot_id!=b.shot_id for a,b in zip(plan.shared_usage_plan,plan.shared_usage_plan[1:]) if b.reused))

    def test_reference_sha_mismatch_is_reported(self):
        value=storyboard(2,40); plan=VideoCoveragePlanner().plan({"a":40},value,capability(10),
            VideoCoverageConfiguration(policy="full_generation"))
        # Give every shot the same recurring cast, then intentionally vary its canonical reference hash.
        shots=tuple(shot.model_copy(update={"recurring_character_ids":("recurring",)}) for shot in plan.unique_shots)
        plan=plan.model_copy(update={"unique_shots":shots})
        hashes={shot.shot_id:("a"*64 if index==0 else "b"*64) for index,shot in enumerate(shots)}
        self.assertIn("recurring_reference_sha_mismatch",VideoCoveragePlanValidator().validate(plan,value,hashes))


if __name__=="__main__": unittest.main()
