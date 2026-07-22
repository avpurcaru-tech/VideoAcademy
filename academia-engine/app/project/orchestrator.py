import os
from datetime import datetime,timezone
from pathlib import Path

from app.media import AudioVideoDurationPolicy
from app.music import MusicGenerationRequest
from app.production import EpisodeTransitionPolicy
from app.production import (EpisodeGenerationRequestResolutionError,EpisodeProviderConfigurationError,
                            EpisodeProviderUnavailableError,EpisodeSceneSubmissionError,EpisodeScenePollingError,
                            EpisodeSceneDownloadError,EpisodeProductionRegistryError,ProductionRegistry)
from app.song import EducationalSongBrief,LyricsPlan,MusicPlan,persist_lyrics_atomic

from .contracts import ProjectFailureStage,ProjectRecord,ProjectStatus
from .registry import ProjectRegistry


class ProjectOrchestrationError(RuntimeError): pass
class ProjectResumeBlockedError(ProjectOrchestrationError): pass


class ProjectGenerationService:
    def __init__(self,services,registry: ProjectRegistry,progress=None): self._services=services; self._registry=registry; self._progress=progress or (lambda value:None)

    def preflight(self,episode,project_id,output_root,video_provider="kling",series_id=None):
        root=Path(output_root); production_id=f"{project_id}-video"
        plan=self._services.director_engine.create_plan(episode)
        self._services.episode_planner.preflight(plan,production_id,root/"video"/"scenes",root/"video"/"workspace",
            root/"video"/"master.mp4",provider=video_provider,transition=EpisodeTransitionPolicy(kind="cut"))
        return self._new_record(episode.id,project_id,root,series_id)

    def mark_storyboard_generating(self,project_id):
        return self._status(self._registry.load(project_id),ProjectStatus.STORYBOARD_GENERATING)

    def generate(self,episode,brief: EducationalSongBrief,music_plan: MusicPlan,project_id,output_root,
                 video_policy,music_policy,video_provider="kling",music_provider="sunoapi_org",series_id=None):
        if self._registry.exists(project_id):
            record=self._registry.load(project_id)
            if record.status != ProjectStatus.PLANNED: raise ProjectOrchestrationError("Project already exists; use resume.")
        else:
            record=self._new_record(episode.id,project_id,Path(output_root),series_id); self._registry.create(record)
        try: self._persist_inputs(record,episode,brief,music_plan)
        except Exception:
            try: self._update(record,status=ProjectStatus.FAILED,failure_stage=ProjectFailureStage.EPISODE_GENERATION,
                failure_category="project_input_persistence_failed",safe_message="Project inputs could not be persisted safely.")
            except Exception: pass
            raise
        return self._run(record,episode,brief,music_plan,video_policy,music_policy,video_provider,music_provider,False)

    def generate_storyboard(self,storyboard,project_id,video_policy,music_policy,video_provider="kling",music_provider="sunoapi_org"):
        from app.storyboard import CreativeStoryboard
        record=self._registry.load(project_id); storyboard=CreativeStoryboard.model_validate(storyboard)
        self._persist_model(record.lyrics_path.parent.parent/"input"/"storyboard.json",storyboard)
        record=self._status(record,ProjectStatus.STORYBOARD_READY)
        return self._run_storyboard(record,storyboard,video_policy,music_policy,video_provider,music_provider)

    def _run_storyboard(self,record,storyboard,video_policy,music_policy,video_provider,music_provider):
        from app.composition import MusicTimelineCompositionRequest,StoryboardVideoClip
        from app.music import MusicGenerationRequest
        from app.music_timeline import MusicTimeline
        from app.production import ProductionRegistry
        from app.storyboard import StoryboardLyricsAdapter,StoryboardMusicAdapter
        try:
            self._progress("Lyrics...")
            if record.lyrics_path.is_file(): lyrics=LyricsPlan.model_validate_json(record.lyrics_path.read_text(encoding="utf-8"))
            else:
                lyrics=StoryboardLyricsAdapter().adapt(storyboard); persist_lyrics_atomic(lyrics,record.lyrics_path)
            music_plan_path=record.lyrics_path.parent.parent/"input"/"music-plan.json"
            if music_plan_path.is_file(): music_plan=MusicPlan.model_validate_json(music_plan_path.read_text(encoding="utf-8"))
            else:
                music_plan=StoryboardMusicAdapter().music_plan(storyboard); self._persist_model(music_plan_path,music_plan)
            record=self._status(record,ProjectStatus.LYRICS_READY)
            music_record=None
            if record.music_task_id:
                music_record=self._services.music_registry.load(record.music_task_id)
                if music_record.artifact_set is None or not music_record.artifact_set.complete:
                    terminal=self._services.music_engine.wait_until_terminal(record.music_task_id,music_policy)
                    if terminal.normalized_status.value=="failed": raise ProjectOrchestrationError("Music task failed.")
                    music_record=self._services.music_engine.download_all_variants(record.music_task_id,record.music_directory)
            else:
                self._progress("Music...")
                record=self._status(record,ProjectStatus.MUSIC_GENERATING)
                request=MusicGenerationRequest(song_id=lyrics.song_id,title=lyrics.title,lyrics=lyrics,music_plan=music_plan)
                music_record=self._services.music_engine.generate_all_variants(request,record.music_directory,music_policy,music_provider)
                record=self._update(record,music_task_id=music_record.provider_task_id)
            record=self._status(record,ProjectStatus.MUSIC_READY)
            timelines=[]; self._progress("Timelines..."); record=self._status(record,ProjectStatus.TIMELINES_GENERATING)
            for index,audio in enumerate(music_record.artifact_set.artifacts,start=1):
                path=record.music_directory/f"timeline-variant-{index:02d}.json"
                if path.is_file(): timeline=MusicTimeline.model_validate_json(path.read_text(encoding="utf-8"))
                else:
                    duration=self._services.audio_probe.probe_audio(audio.local_path).duration_seconds
                    timeline=self._services.music_timeline_service.generate(storyboard,lyrics,duration)
                    timeline=timeline.model_copy(update={"timeline_id":f"{storyboard.storyboard_id}-variant-{index:02d}"})
                    self._persist_model(path,timeline)
                timelines.append(timeline)
            record=self._status(record,ProjectStatus.TIMELINES_READY)
            production=None
            try: production=ProductionRegistry().load(record.video_production_id)
            except Exception: pass
            if production is None or any(scene.local_path is None for scene in production.scenes):
                self._progress("Video...")
                record=self._status(record,ProjectStatus.VIDEO_PLANNING)
                record=self._status(record,ProjectStatus.VIDEO_GENERATING)
                result=self._services.episode_generation_service.plan_and_produce(storyboard,video_policy,
                    production_id=record.video_production_id,scene_output_directory=record.video_directory/"scenes",
                    workspace=record.video_directory/"workspace",destination=record.video_directory/"master.mp4",
                    provider=video_provider,transition=EpisodeTransitionPolicy(kind="cut"))
                production=ProductionRegistry().load(record.video_production_id)
            record=self._status(record,ProjectStatus.VIDEO_READY)
            clips=tuple(StoryboardVideoClip(storyboard_section_id=scene.source_scene_id,
                local_path=scene.local_path) for scene in production.scenes)
            self._progress("Composition..."); record=self._status(record,ProjectStatus.COMPOSING)
            for index,(audio,timeline) in enumerate(zip(music_record.artifact_set.artifacts,timelines,strict=True),start=1):
                destination=record.final_directory/f"final-variant-{index:02d}.mp4"
                if destination.is_file(): continue
                self._services.music_timeline_composer.compose(MusicTimelineCompositionRequest(
                    composition_id=f"{record.project_id}-variant-{index:02d}",timeline=timeline,video_clips=clips,
                    music_source=audio.local_path,destination=destination,
                    workspace=record.final_directory/f"workspace-variant-{index:02d}",resume=True))
            return self._status(record,ProjectStatus.COMPLETED)
        except Exception as error:
            try: self._update(record,status=ProjectStatus.FAILED,failure_stage=self._storyboard_run_failure_stage(record),
                failure_category="generation_failed",safe_message="Storyboard-first project generation failed at a safe boundary.")
            except Exception: pass
            raise

    @staticmethod
    def _storyboard_run_failure_stage(record):
        if record.status in (ProjectStatus.MUSIC_GENERATING,ProjectStatus.MUSIC_READY): return ProjectFailureStage.MUSIC_GENERATION
        if record.status in (ProjectStatus.TIMELINES_GENERATING,ProjectStatus.TIMELINES_READY): return ProjectFailureStage.MUSIC_GENERATION
        if record.status in (ProjectStatus.VIDEO_PLANNING,ProjectStatus.VIDEO_GENERATING): return ProjectFailureStage.VIDEO_PLANNING
        if record.status==ProjectStatus.COMPOSING: return ProjectFailureStage.COMPOSITION
        return ProjectFailureStage.LYRICS_GENERATION

    @staticmethod
    def _persist_model(destination,model):
        destination=Path(destination); destination.parent.mkdir(parents=True,exist_ok=True); temporary=destination.with_suffix(destination.suffix+".part")
        try:
            temporary.write_text(model.model_dump_json(indent=2),encoding="utf-8")
            with temporary.open("r+b") as stream: os.fsync(stream.fileno())
            os.replace(temporary,destination)
        finally: temporary.unlink(missing_ok=True)

    def _run(self,record,episode,brief,music_plan,video_policy,music_policy,video_provider,music_provider,resume):
        try:
            master=record.video_directory/"master.mp4"
            if not master.is_file():
                record=self._status(record,ProjectStatus.VIDEO_GENERATING)
                if resume:
                    result=self._services.video_resumer.resume(record.video_production_id,video_policy)
                else:
                    plan=self._services.director_engine.create_plan(episode)
                    result=self._services.episode_generation_service.plan_and_produce(plan,video_policy,
                        production_id=record.video_production_id,scene_output_directory=record.video_directory/"scenes",
                        workspace=record.video_directory/"workspace",destination=master,provider=video_provider,
                        transition=EpisodeTransitionPolicy(kind="cut"))
                if not master.is_file() and getattr(result,"final_artifact",None) is None:
                    raise ProjectOrchestrationError("Video stage did not produce a master artifact.")
            record=self._status(record,ProjectStatus.MUSIC_GENERATING)
            if record.lyrics_path.is_file(): lyrics=LyricsPlan.model_validate_json(record.lyrics_path.read_text(encoding="utf-8"))
            else:
                lyrics=self._services.lyrics_generation_service.generate(brief)
                persist_lyrics_atomic(lyrics,record.lyrics_path)
            music_record=None
            if record.music_task_id:
                try: music_record=self._services.music_registry.load(record.music_task_id)
                except Exception: music_record=None
                if music_record is None or music_record.artifact_set is None or not music_record.artifact_set.complete:
                    terminal=self._services.music_engine.wait_until_terminal(record.music_task_id,music_policy)
                    if terminal.normalized_status.value=="failed": raise ProjectOrchestrationError("Music task failed.")
                    music_record=self._services.music_engine.download_all_variants(record.music_task_id,record.music_directory)
            else:
                request=MusicGenerationRequest(song_id=lyrics.song_id,title=lyrics.title,lyrics=lyrics,music_plan=music_plan)
                try:
                    music_record=self._services.music_engine.generate_all_variants(request,record.music_directory,music_policy,music_provider)
                except Exception as error:
                    task_id=getattr(error,"provider_task_id",None)
                    if isinstance(task_id,str) and task_id: record=self._update(record,music_task_id=task_id)
                    raise
                record=self._update(record,music_task_id=music_record.provider_task_id)
            record=self._status(record,ProjectStatus.COMPOSING)
            audios=music_record.artifact_set.artifacts
            for index,audio in enumerate(audios,start=1):
                destination=record.final_directory/f"final-variant-{index:02d}.mp4"
                if destination.is_file(): continue
                self._services.audio_variant_video_composer.compose_variants(master,[audio],record.final_directory,
                    record.final_directory/"workspace",AudioVideoDurationPolicy.EXTEND_VIDEO_TO_AUDIO,start_index=index)
            return self._status(record,ProjectStatus.COMPLETED)
        except Exception as error:
            try:
                stage,category,message,scene_id=self._failure_details(error,record)
                diagnostic=self._video_submit_diagnostic(record)
                self._update(record,status=ProjectStatus.FAILED,failure_stage=stage,
                    failure_category=category,safe_message=message,failed_scene_id=scene_id,**diagnostic)
            except Exception: pass
            raise

    @staticmethod
    def _failure_details(error,record):
        mappings=(
            (EpisodeGenerationRequestResolutionError,ProjectFailureStage.VIDEO_REQUEST_RESOLUTION,"video_request_resolution_failed"),
            (EpisodeProviderConfigurationError,ProjectFailureStage.VIDEO_PROVIDER_CONFIGURATION,"video_provider_configuration_missing"),
            (EpisodeProviderUnavailableError,ProjectFailureStage.VIDEO_PROVIDER_CONFIGURATION,"video_provider_unavailable"),
            (EpisodeSceneSubmissionError,ProjectFailureStage.VIDEO_SUBMISSION,"video_submission_failed"),
            (EpisodeScenePollingError,ProjectFailureStage.VIDEO_POLLING,"video_polling_failed"),
            (EpisodeSceneDownloadError,ProjectFailureStage.VIDEO_DOWNLOAD,"video_download_failed"),
            (EpisodeProductionRegistryError,ProjectFailureStage.VIDEO_SUBMISSION,"video_registry_persistence_failed"),
        )
        for kind,stage,category in mappings:
            if isinstance(error,kind): return stage,category,str(error),ProjectGenerationService._failed_scene(record)
        if record.status==ProjectStatus.VIDEO_GENERATING:
            return ProjectFailureStage.VIDEO_PLANNING,"video_stage_failed","Video generation failed at a safe boundary.",ProjectGenerationService._failed_scene(record)
        if record.status==ProjectStatus.MUSIC_GENERATING:
            stage=ProjectFailureStage.LYRICS_GENERATION if not record.lyrics_path.is_file() else ProjectFailureStage.MUSIC_GENERATION
            return stage,"generation_failed",("Lyrics generation failed at a safe boundary." if stage==ProjectFailureStage.LYRICS_GENERATION else "Music generation failed at a safe boundary."),None
        if record.status==ProjectStatus.COMPOSING:
            return ProjectFailureStage.COMPOSITION,"composition_failed","Composition failed at a safe boundary.",None
        return ProjectFailureStage.EPISODE_GENERATION,"project_generation_failed","Project generation failed at a safe boundary.",None

    @staticmethod
    def _failed_scene(record):
        try: return ProductionRegistry().load(record.video_production_id).failed_scene_id
        except Exception: return None

    @staticmethod
    def _video_submit_diagnostic(record):
        try:
            production=ProductionRegistry().load(record.video_production_id)
            return {"submit_http_status":production.submit_http_status,
                "submit_provider_code":production.submit_provider_code,
                "submit_provider_task_id":production.submit_provider_task_id,
                "submit_response_shape":production.submit_response_shape,
                "query_http_status":production.query_http_status,
                "query_provider_code":production.query_provider_code,
                "query_provider_task_id":production.query_provider_task_id,
                "query_response_shape":production.query_response_shape}
        except Exception:
            return {}

    def _new_record(self,episode_id,project_id,root,series_id=None):
        now=datetime.now(timezone.utc)
        return ProjectRecord(project_id=project_id,episode_id=episode_id,series_id=series_id,status=ProjectStatus.PLANNED,
            video_production_id=f"{project_id}-video",lyrics_path=root/"lyrics"/"lyrics.json",
            music_directory=root/"music",video_directory=root/"video",final_directory=root/"final",
            created_at=now,updated_at=now)
    @classmethod
    def create_planned(cls,registry,project_id,root,episode_id,series_id=None):
        """Create the confirmed project's durable boundary before any provider or creative resolution."""
        record=cls(None,registry)._new_record(episode_id,project_id,Path(root),series_id).model_copy(update={"orchestration_version":"storyboard_first"})
        registry.create(record)
        project_root=record.lyrics_path.parent.parent
        for directory in (project_root/"input",project_root/"lyrics",record.music_directory,record.video_directory,
                          record.final_directory,project_root/"logs"):
            directory.mkdir(parents=True,exist_ok=True)
        return record
    @staticmethod
    def persist_creative_brief(record,brief,character_ids=()):
        root=record.lyrics_path.parent.parent/"input"
        root.mkdir(parents=True,exist_ok=True)
        payload=brief.model_dump(mode="json")
        payload["resolved_character_ids"]=list(character_ids)
        destination=root/"creative-brief.json"; temporary=destination.with_suffix(".json.part")
        try:
            with temporary.open("w",encoding="utf-8") as stream:
                import json
                json.dump(payload,stream,ensure_ascii=False,indent=2); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary,destination)
        finally: temporary.unlink(missing_ok=True)
    @staticmethod
    def fail_planned(registry,project_id,stage,category,message):
        record=registry.load(project_id)
        updated=record.model_copy(update={"status":ProjectStatus.FAILED,"failure_stage":stage,
            "failure_category":category,"safe_message":message,"updated_at":datetime.now(timezone.utc)})
        registry.update(updated)
        return updated
    def _status(self,record,status): return self._update(record,status=status)
    def _update(self,record,**values):
        updated=record.model_copy(update={**values,"updated_at":datetime.now(timezone.utc)}); self._registry.update(updated); return updated
    def _persist_inputs(self,record,episode,brief,music_plan):
        project_root=record.lyrics_path.parent.parent
        for directory in (project_root/"input",project_root/"lyrics",record.music_directory,record.video_directory,
                          record.final_directory,project_root/"logs"): directory.mkdir(parents=True,exist_ok=True)
        root=project_root/"input"
        for name,model in (("episode.json",episode),("song-brief.json",brief),("music-plan.json",music_plan)):
            destination=root/name; temporary=destination.with_suffix(".json.part")
            try:
                temporary.write_text(model.model_dump_json(indent=2),encoding="utf-8")
                with temporary.open("r+b") as stream: os.fsync(stream.fileno())
                os.replace(temporary,destination)
            finally: temporary.unlink(missing_ok=True)


class ProjectResumeService:
    def __init__(self,generation_service: ProjectGenerationService,registry: ProjectRegistry):
        self._generation=generation_service; self._registry=registry
    def resume(self,project_id,video_policy,music_policy,video_provider="kling",music_provider="sunoapi_org"):
        record=self._registry.load(project_id)
        if record.status==ProjectStatus.COMPLETED: return record
        root=record.lyrics_path.parent.parent; inputs=root/"input"
        if record.orchestration_version=="storyboard_first":
            try:
                from app.storyboard import CreativeStoryboard
                storyboard=CreativeStoryboard.model_validate_json((inputs/"storyboard.json").read_text(encoding="utf-8"))
            except Exception as error: raise ProjectResumeBlockedError("Durable storyboard is unavailable.") from error
            return self._generation._run_storyboard(record,storyboard,video_policy,music_policy,video_provider,music_provider)
        try:
            from app.models import Episode
            episode=Episode.model_validate_json((inputs/"episode.json").read_text(encoding="utf-8"))
            brief=EducationalSongBrief.model_validate_json((inputs/"song-brief.json").read_text(encoding="utf-8"))
            music=MusicPlan.model_validate_json((inputs/"music-plan.json").read_text(encoding="utf-8"))
        except Exception as error: raise ProjectResumeBlockedError("Durable project inputs are unavailable.") from error
        return self._generation._run(record,episode,brief,music,video_policy,music_policy,video_provider,music_provider,True)
