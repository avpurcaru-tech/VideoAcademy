import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

from app.cli.project_generate import main as generate_cli
from app.media import AudioVideoComposedArtifact,MediaProbeResult
from app.models import Episode,GenerationTaskStatus
from app.music import DurableAudioArtifact,DurableAudioArtifactSet,MusicGenerationTaskRecord
from app.project import (ProjectGenerationService,ProjectRecord,ProjectRegistry,ProjectRegistryError,
                         ProjectResumeService,ProjectServices,ProjectStatus)
from app.song import EducationalSongBrief,LyricsPlan,MusicPlan


NOW="2026-07-21T12:00:00Z"


class ProjectOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)/"counting-1-to-5"
        examples=Path(__file__).resolve().parents[1]/"examples"/"smoke"
        self.episode=Episode.model_validate_json((examples/"episode-input.json").read_text(encoding="utf-8"))
        source_brief=EducationalSongBrief.model_validate_json((examples/"song-brief.json").read_text(encoding="utf-8"))
        source_music=MusicPlan.model_validate_json((examples/"music-plan.json").read_text(encoding="utf-8"))
        self.brief=source_brief.model_copy(update={"song_id":"counting-1-to-5"}); self.music_plan=source_music
        self.lyrics=LyricsPlan.model_validate_json((examples/"lyrics-plan.json").read_text(encoding="utf-8"))
        self.registry=ProjectRegistry(self.root.parent)
        self.director=Mock(); self.director.create_plan.return_value=Mock()
        self.planner=Mock(); self.planner.preflight.return_value=Mock()
        self.video=Mock(); self.resumer=Mock(); self.lyrics_service=Mock(); self.lyrics_service.generate.return_value=self.lyrics
        self.music=Mock(); self.music_registry=Mock(); self.composer=Mock()
        self.services=ProjectServices(self.director,self.planner,self.video,self.resumer,self.lyrics_service,
                                     self.music,self.composer,self.music_registry)
        self.service=ProjectGenerationService(self.services,self.registry)
        self._configure_outputs()

    def tearDown(self): self.temp.cleanup()

    def _configure_outputs(self):
        def video_side(*args,**kwargs):
            master=Path(kwargs["destination"]); master.parent.mkdir(parents=True,exist_ok=True); master.write_bytes(b"video")
            return Mock(final_artifact=Mock(local_path=master))
        self.video.plan_and_produce.side_effect=video_side
        def resume_side(task_id,policy):
            master=self.root/"video"/"master.mp4"; master.parent.mkdir(parents=True,exist_ok=True); master.write_bytes(b"video")
            return Mock(final_artifact=Mock(local_path=master))
        self.resumer.resume.side_effect=resume_side
        self.audios=tuple(DurableAudioArtifact(artifact_id=f"audio-{i}",local_path=self.root/"music"/f"variant-{i:02d}.mp3",
            byte_size=10,sha256=str(i)*64,content_type="audio/mpeg",variant_index=i) for i in (1,2))
        for audio in self.audios: audio.local_path.parent.mkdir(parents=True,exist_ok=True); audio.local_path.write_bytes(b"audio")
        self.music_record=MusicGenerationTaskRecord(provider="sunoapi_org",provider_task_id="music-task",
            normalized_status=GenerationTaskStatus.SUCCEEDED,created_at=NOW,updated_at=NOW,
            artifact_set=DurableAudioArtifactSet(provider_task_id="music-task",artifacts=self.audios,
                expected_artifact_count=2,complete=True))
        self.music.generate_all_variants.return_value=self.music_record; self.music_registry.load.return_value=self.music_record
        def compose(video,audios,directory,workspace,policy,start_index=1):
            output=Path(directory)/f"final-variant-{start_index:02d}.mp4"; output.parent.mkdir(parents=True,exist_ok=True); output.write_bytes(b"final")
            info=MediaProbeResult(local_path=output,duration_seconds=10,width=1280,height=720,frame_rate=30,
                video_codec="h264",audio_codec="aac",has_audio=True,container_format="mp4")
            return (AudioVideoComposedArtifact(local_path=output,byte_size=5,sha256="a"*64,media_info=info,
                video_source_path=Path(video),audio_source_path=audios[0].local_path,duration_policy=policy),)
        self.composer.compose_variants.side_effect=compose

    def _generate(self):
        return self.service.generate(self.episode,self.brief,self.music_plan,"counting-1-to-5",self.root,
                                     Mock(),Mock(),"kling","sunoapi_org")

    def test_fresh_project_persists_status_paths_and_two_outputs(self):
        record=self._generate()
        self.assertEqual(record.status,ProjectStatus.COMPLETED); self.assertEqual(record.music_task_id,"music-task")
        self.assertEqual(self.registry.load(record.project_id),record)
        self.assertTrue((self.root/"video"/"master.mp4").is_file()); self.assertTrue((self.root/"lyrics"/"lyrics.json").is_file())
        self.assertTrue((self.root/"final"/"final-variant-01.mp4").is_file()); self.assertTrue((self.root/"final"/"final-variant-02.mp4").is_file())
        manifest=(self.root/"project.json").read_text(encoding="utf-8")
        for forbidden in ("prompt","audio_url","https://","Authorization"): self.assertNotIn(forbidden,manifest)

    def test_resume_after_video_skips_video_and_generates_remaining_stages(self):
        self._generate(); (self.root/"lyrics"/"lyrics.json").unlink()
        for path in (self.root/"final").glob("*.mp4"): path.unlink()
        record=self.registry.load("counting-1-to-5").model_copy(update={"status":ProjectStatus.FAILED,"music_task_id":None})
        self.registry.update(record); self.video.reset_mock(); self.resumer.reset_mock(); self.lyrics_service.reset_mock()
        resumed=ProjectResumeService(self.service,self.registry).resume("counting-1-to-5",Mock(),Mock())
        self.assertEqual(resumed.status,ProjectStatus.COMPLETED); self.video.plan_and_produce.assert_not_called()
        self.resumer.resume.assert_not_called(); self.lyrics_service.generate.assert_called_once()

    def test_resume_after_lyrics_skips_video_and_lyrics(self):
        self._generate()
        for path in (self.root/"final").glob("*.mp4"): path.unlink()
        record=self.registry.load("counting-1-to-5").model_copy(update={"status":ProjectStatus.FAILED,"music_task_id":None})
        self.registry.update(record); self.video.reset_mock(); self.lyrics_service.reset_mock(); self.music.reset_mock()
        self.music.generate_all_variants.return_value=self.music_record
        ProjectResumeService(self.service,self.registry).resume("counting-1-to-5",Mock(),Mock())
        self.video.plan_and_produce.assert_not_called(); self.lyrics_service.generate.assert_not_called()
        self.music.generate_all_variants.assert_called_once()

    def test_resume_after_music_and_after_first_composition_only_composes_missing(self):
        self._generate(); self.composer.reset_mock(); (self.root/"final"/"final-variant-02.mp4").unlink()
        record=self.registry.load("counting-1-to-5").model_copy(update={"status":ProjectStatus.FAILED}); self.registry.update(record)
        resumed=ProjectResumeService(self.service,self.registry).resume("counting-1-to-5",Mock(),Mock())
        self.assertEqual(resumed.status,ProjectStatus.COMPLETED); self.music.generate_all_variants.assert_called_once()
        self.assertEqual(self.composer.compose_variants.call_count,1)
        self.assertEqual(self.composer.compose_variants.call_args.kwargs["start_index"],2)

    def test_completed_resume_is_idempotent_and_calls_nothing(self):
        completed=self._generate(); self.video.reset_mock(); self.music.reset_mock(); self.composer.reset_mock()
        result=ProjectResumeService(self.service,self.registry).resume("counting-1-to-5",Mock(),Mock())
        self.assertEqual(result,completed); self.video.assert_not_called(); self.music.assert_not_called(); self.composer.assert_not_called()

    def test_registry_atomic_failure_preserves_previous_record(self):
        record=ProjectRecord(project_id="atomic",episode_id="episode",status="planned",video_production_id="atomic-video",
            lyrics_path=self.root/"lyrics.json",music_directory=self.root/"music",video_directory=self.root/"video",
            final_directory=self.root/"final",created_at=NOW,updated_at=NOW)
        self.registry.create(record)
        with patch("app.project.registry.os.replace",side_effect=OSError("SECRET")),self.assertRaises(ProjectRegistryError):
            self.registry.update(record.model_copy(update={"status":ProjectStatus.FAILED}))
        self.assertEqual(self.registry.load("atomic").status,ProjectStatus.PLANNED)

    def test_music_failure_with_durable_task_id_is_attached_to_project(self):
        error=RuntimeError("SECRET"); error.provider_task_id="recoverable-task"
        self.music.generate_all_variants.side_effect=error
        with self.assertRaises(RuntimeError): self._generate()
        record=self.registry.load("counting-1-to-5")
        self.assertEqual(record.music_task_id,"recoverable-task"); self.assertEqual(record.status,ProjectStatus.FAILED)

    def test_generate_cli_without_confirm_is_preflight_only_and_safe(self):
        episode_path=Path(__file__).resolve().parents[1]/"examples"/"smoke"/"episode-input.json"
        director=Mock(); director.create_plan.return_value=Mock(); planner=Mock(); planner.preflight.return_value=Mock()
        services=ProjectServices(director,planner,None,None,None,None,None,None)
        output_root=self.root.parent/"safe-project"
        argv=["project_generate","--episode",str(episode_path),"--project-id","safe-project","--video-provider","kling",
              "--lyrics-provider","openai","--music-provider","sunoapi_org","--output",str(output_root)]
        with patch("sys.argv",argv),patch("app.cli.project_generate.load_application_environment"),patch(
                "app.cli.project_generate.build_services",return_value=services),patch("builtins.print") as emit:
            self.assertEqual(generate_cli(),2)
        output="\n".join(str(call.args[0]) for call in emit.call_args_list)
        self.assertIn("Preflight passed.",output); self.assertNotIn("prompt",output.lower())
        self.assertFalse((output_root/"project.json").exists())


if __name__=="__main__": unittest.main()
