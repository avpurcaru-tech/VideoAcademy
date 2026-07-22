import tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch

from app.creative import EducationalCreativeBrief
from app.music_timeline import MusicTimeline,MusicTimelineSegment
from app.project import ProjectGenerationService,ProjectRegistry,ProjectServices,ProjectStatus
from app.storyboard import DeterministicStoryboardGenerator


class StoryboardFirstOrchestrationTests(unittest.TestCase):
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
            production=SimpleNamespace(scenes=scenes)
            video=Mock(); video.plan_and_produce.side_effect=lambda *a,**k:events.append("video")
            composer=Mock()
            def compose(request):
                events.append(f"compose-{request.timeline.music_duration_seconds:g}")
                request.destination.parent.mkdir(parents=True,exist_ok=True); request.destination.write_bytes(b"final")
            composer.compose.side_effect=compose
            services=ProjectServices(Mock(),Mock(),video,Mock(),Mock(),music,Mock(),Mock(),probe,timelines,composer)
            service=ProjectGenerationService(services,registry)
            with patch("app.production.ProductionRegistry") as productions:
                productions.return_value.load.side_effect=[RuntimeError("missing"),production]
                result=service.generate_storyboard(storyboard,"project",Mock(),Mock())
            self.assertEqual(ProjectStatus.COMPLETED,result.status)
            self.assertEqual(["music","timeline-21","timeline-23","video","compose-21","compose-23"],events)
            self.assertTrue((root/"music"/"timeline-variant-01.json").is_file())
            self.assertTrue((root/"music"/"timeline-variant-02.json").is_file())
            self.assertEqual(1,video.plan_and_produce.call_count)


if __name__=="__main__": unittest.main()
