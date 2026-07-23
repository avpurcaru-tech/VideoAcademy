import io,json,tempfile,unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock,patch

from app.cli.project_scene_first_frame_preflight import main as preflight_main
from app.creative import EducationalCreativeBrief
from app.project import ProjectGenerationService,ProjectNotFoundError,ProjectRegistry,ProjectStatus
from app.scene_first_frames import SceneFirstFramePlan
from app.storyboard import DeterministicStoryboardGenerator
from app.video_coverage import VideoCoverageConfiguration,VideoCoveragePlanner,VideoProviderCapabilities


class ProjectPlanOnlyTests(unittest.TestCase):
    def test_nonexistent_project_has_exact_category(self):
        registry=Mock(); registry.load.side_effect=ProjectNotFoundError("missing")
        output=io.StringIO()
        with patch("sys.argv",["preflight","--project-id","missing"]),patch(
                "app.cli.project_scene_first_frame_preflight.ProjectRegistry",return_value=registry),redirect_stdout(output):
            self.assertEqual(1,preflight_main())
        self.assertIn("Failure category: project_not_found",output.getvalue())

    def test_planning_preflight_succeeds_without_generated_images(self):
        brief=EducationalCreativeBrief(brief_id="story",topic="colors",learning_objectives=("colors",),language="en",
            target_age_min=3,target_age_max=5,target_duration_seconds=20,tone="warm",visual_style="3D",scene_count=2,song_required=True)
        storyboard=DeterministicStoryboardGenerator().generate_storyboard(brief)
        capabilities=VideoProviderCapabilities(provider_name="kling_image_to_video",supported_clip_durations=(10,),
            selected_clip_duration=10,supports_reference_images=True,supports_multiple_references=False)
        coverage=VideoCoveragePlanner().plan({"variant-01":20},storyboard,capabilities,VideoCoverageConfiguration(policy="full_generation"))
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/"project"; registry=ProjectRegistry(root.parent)
            record=ProjectGenerationService.create_planned(registry,"project",root,"story",video_provider="kling_image_to_video")
            inputs=root/"input"; (inputs/"storyboard.json").write_text(storyboard.model_dump_json(),encoding="utf-8")
            (inputs/"video-coverage-plan.json").write_text(coverage.model_dump_json(),encoding="utf-8")
            (root/"music"/"variant-01.mp3").write_bytes(b"audio")
            (root/"music"/"timeline-variant-01.json").write_text("{}",encoding="utf-8")
            plans=[]
            for shot in coverage.unique_shots:
                plans.append(SceneFirstFramePlan(first_frame_id=f"project-{shot.shot_id}-first-frame",shot_id=shot.shot_id,
                    source_storyboard_section_id=shot.source_storyboard_section_id,
                    recurring_character_ids=shot.recurring_character_ids,
                    canonical_reference_sha256=tuple("a"*64 for _ in shot.recurring_character_ids),background="sunny park",
                    required_objects=("red ball",),character_positions="characters beside ball",camera_framing=shot.camera_variation,
                    visual_style="3D preschool",width=1920,height=1080))
            plan_path=inputs/"scene-first-frame-plans.json"
            plan_path.write_text(json.dumps({"plans":[value.model_dump(mode="json") for value in plans]}),encoding="utf-8")
            registry.update(record.model_copy(update={"status":ProjectStatus.AWAITING_SCENE_FIRST_FRAME_GENERATION,
                "video_coverage_plan_path":inputs/"video-coverage-plan.json","scene_first_frame_plan_path":plan_path,
                "unique_shot_ids":tuple(value.shot_id for value in plans)}))
            output=io.StringIO()
            with patch("sys.argv",["preflight","--project-id","project"]),patch(
                    "app.cli.project_scene_first_frame_preflight.ProjectRegistry",return_value=registry),patch(
                    "app.cli.project_scene_first_frame_preflight.SceneFirstFrameStore") as frames,redirect_stdout(output):
                frames.return_value.load.return_value=None
                self.assertEqual(0,preflight_main())
            text=output.getvalue(); self.assertIn("Contextual frame present: no",text)
            self.assertIn("Publication URL available: no",text); self.assertIn("Ready for frame generation: yes",text)
            self.assertIn("Kling calls: 0",text)

    def test_missing_artifact_categories_are_not_collapsed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/"project"; registry=ProjectRegistry(root.parent)
            ProjectGenerationService.create_planned(registry,"project",root,"story")
            output=io.StringIO()
            with patch("sys.argv",["preflight","--project-id","project"]),patch(
                    "app.cli.project_scene_first_frame_preflight.ProjectRegistry",return_value=registry),redirect_stdout(output):
                self.assertEqual(1,preflight_main())
            self.assertIn("Failure category: storyboard_missing",output.getvalue())


if __name__=="__main__": unittest.main()
