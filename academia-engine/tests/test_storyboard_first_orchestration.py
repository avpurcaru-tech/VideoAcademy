import tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch

from app.creative import EducationalCreativeBrief
from app.music_timeline import MusicTimeline,MusicTimelineSegment
from app.project import ProjectFailureStage,ProjectGenerationService,ProjectRegistry,ProjectServices,ProjectStatus
from app.production import ProductionFailureStage
from app.storyboard import DeterministicStoryboardGenerator
from app.production import StoryboardVideoPlanner


class StoryboardFirstOrchestrationTests(unittest.TestCase):
    def test_different_variant_durations_are_valid_when_section_order_matches(self):
        brief=EducationalCreativeBrief(brief_id="story",topic="x",learning_objectives=("x",),language="en",
            target_age_min=3,target_age_max=5,target_duration_seconds=20,tone="warm",visual_style="simple",scene_count=2,song_required=True)
        storyboard=DeterministicStoryboardGenerator().generate_storyboard(brief)
        def value(duration):
            return MusicTimeline(timeline_id=f"t-{duration}",storyboard_id="story",music_duration_seconds=duration,
                segments=(MusicTimelineSegment(start_seconds=0,end_seconds=duration/2,storyboard_section_id=storyboard.sections[0].section_id,estimated_confidence=1),
                    MusicTimelineSegment(start_seconds=duration/2,end_seconds=duration,storyboard_section_id=storyboard.sections[1].section_id,estimated_confidence=1)))
        ProjectGenerationService._validate_timeline_mapping(storyboard,(value(21),value(27)))
        wrong=value(21).model_copy(update={"segments":tuple(reversed(value(21).segments))})
        with self.assertRaises(Exception): ProjectGenerationService._validate_timeline_mapping(storyboard,(value(21),wrong))

    def test_http_400_production_diagnostic_remains_video_submission(self):
        record=SimpleNamespace(status=ProjectStatus.VIDEO_GENERATING,video_production_id="project-video")
        production=SimpleNamespace(failure_stage=ProductionFailureStage.VIDEO_SUBMISSION,
            failure_category="video_request_rejected",failed_scene_id="scene-0004",submit_http_status=400,
            submit_provider_code=1201,submit_provider_message="request rejected",submit_request_id="request-safe",
            submit_provider_task_id=None,submit_response_shape=("root: object","root.code: number"))
        with patch("app.project.orchestrator.ProductionRegistry") as registry:
            registry.return_value.load.return_value=production
            values=ProjectGenerationService._storyboard_video_diagnostic(record,RuntimeError())
        self.assertEqual(ProjectFailureStage.VIDEO_SUBMISSION,values["failure_stage"])
        self.assertEqual("video_request_rejected",values["failure_category"])
        self.assertEqual(400,values["submit_http_status"]); self.assertEqual("scene-0004",values["failed_scene_id"])
        self.assertNotIn("prompt"," ".join(values["submit_response_shape"]))

    def test_exact_music_first_order_two_timelines_shared_video_and_independent_composition(self):
        events=[]
        brief=EducationalCreativeBrief(brief_id="story",topic="counting",learning_objectives=("count",),language="en",
            target_age_min=3,target_age_max=5,target_duration_seconds=20,tone="cheerful",visual_style="simple",
            scene_count=2,song_required=True)
        storyboard=DeterministicStoryboardGenerator().generate_storyboard(brief)
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/"project"; registry=ProjectRegistry(root.parent)
            ProjectGenerationService.create_planned(registry,"project",root,"story")
            audios=[]
            for index,duration in ((1,21.0),(2,23.0)):
                path=root/"music"/f"variant-{index:02d}.mp3"; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(b"audio")
                audios.append(SimpleNamespace(local_path=path))
            music_record=SimpleNamespace(provider_task_id="task",artifact_set=SimpleNamespace(artifacts=tuple(audios),complete=True))
            music=Mock(); music.generate_all_variants.side_effect=lambda *a,**k:(events.append("music") or music_record)
            probe=Mock(); probe.probe_audio.side_effect=lambda path:SimpleNamespace(duration_seconds=21 if "01" in path.name else 23)
            timelines=Mock()
            def timeline(story,lyrics,duration):
                events.append(f"timeline-{duration:g}"); half=duration/2
                return MusicTimeline(timeline_id="temporary",storyboard_id="story",music_duration_seconds=duration,
                    segments=(MusicTimelineSegment(start_seconds=0,end_seconds=half,storyboard_section_id=story.sections[0].section_id,estimated_confidence=1),
                              MusicTimelineSegment(start_seconds=half,end_seconds=duration,storyboard_section_id=story.sections[1].section_id,estimated_confidence=1)))
            timelines.generate.side_effect=timeline
            scenes=tuple(SimpleNamespace(source_scene_id=section.section_id,local_path=root/"video"/f"{section.section_id}.mp4") for section in storyboard.sections)
            production=SimpleNamespace(scenes=scenes,status=SimpleNamespace(value="succeeded"),
                final_artifact=SimpleNamespace(local_path=root/"video"/"master.mp4",media_info=SimpleNamespace(duration_seconds=20)))
            generated=StoryboardVideoPlanner().build(storyboard,"project-video")
            planned=SimpleNamespace(video_requests=generated,source_scene_ids=tuple(s.section_id for s in storyboard.sections),
                generation_request_references=tuple(SimpleNamespace(reference_id=value.request_id) for value in generated))
            video=Mock(); video.plan_only.return_value=planned
            video.produce_planned.side_effect=lambda *a,**k:events.append("video")
            composer=Mock()
            def compose(request):
                events.append(f"compose-{request.timeline.music_duration_seconds:g}")
                request.destination.parent.mkdir(parents=True,exist_ok=True); request.destination.write_bytes(b"final")
                import hashlib
                return SimpleNamespace(local_path=request.destination,byte_size=5,sha256=hashlib.sha256(b"final").hexdigest())
            composer.compose.side_effect=compose
            services=ProjectServices(Mock(),Mock(),video,Mock(),Mock(),music,Mock(),Mock(),probe,timelines,composer)
            service=ProjectGenerationService(services,registry)
            with patch("app.production.ProductionRegistry") as productions,patch(
                    "app.production.reconcile_succeeded_production",return_value=production):
                productions.return_value.load.side_effect=[RuntimeError("missing"),production]
                result=service.generate_storyboard(storyboard,"project",Mock(),Mock())
            self.assertEqual(ProjectStatus.COMPLETED,result.status)
            self.assertEqual(["music","timeline-21","timeline-23","video","compose-21","compose-23"],events)
            self.assertTrue((root/"music"/"timeline-variant-01.json").is_file())
            self.assertTrue((root/"music"/"timeline-variant-02.json").is_file())
            self.assertEqual(1,video.produce_planned.call_count)


if __name__=="__main__": unittest.main()
