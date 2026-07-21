class CreativeProjectGenerationService:
    def __init__(self,episode_service,project_service,project_registry,storyboard_service=None,storyboard_repository=None):
        self._episodes=episode_service; self._projects=project_service; self._registry=project_registry
        self._storyboards=storyboard_service; self._storyboard_repository=storyboard_repository
    def _derive_episode(self,brief):
        if brief.series_id and self._storyboards is not None:
            from app.storyboard import EpisodeService
            storyboard=self._storyboards.generate(brief)
            if self._storyboard_repository is not None:
                try: self._storyboard_repository.save(storyboard)
                except Exception:
                    try:
                        if self._storyboard_repository.load(storyboard.storyboard_id)!=storyboard: raise
                    except Exception: raise
            return EpisodeService().resolve(storyboard)
        return self._episodes.generate(brief)
    def preflight(self,brief,project_id,output_root,video_provider="kling"):
        episode=self._derive_episode(brief)
        return self._projects.preflight(episode,project_id,output_root,video_provider,series_id=brief.series_id)
    def generate(self,brief,project_id,output_root,video_policy,music_policy,video_provider="kling",music_provider="sunoapi_org"):
        if self._registry.exists(project_id): raise RuntimeError("Project already exists; use resume.")
        episode=self._derive_episode(brief)
        from app.cli.project_generate import _semantic_song_inputs
        song_brief,music_plan=_semantic_song_inputs(episode,project_id)
        return self._projects.generate(episode,song_brief,music_plan,project_id,output_root,video_policy,music_policy,
                                       video_provider,music_provider,series_id=brief.series_id)
