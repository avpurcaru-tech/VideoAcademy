class CreativeProjectGenerationService:
    def __init__(self,episode_service,project_service,project_registry):
        self._episodes=episode_service; self._projects=project_service; self._registry=project_registry
    def preflight(self,brief,project_id,output_root,video_provider="kling"):
        episode=self._episodes.generate(brief)
        return self._projects.preflight(episode,project_id,output_root,video_provider)
    def generate(self,brief,project_id,output_root,video_policy,music_policy,video_provider="kling",music_provider="sunoapi_org"):
        if self._registry.exists(project_id): raise RuntimeError("Project already exists; use resume.")
        episode=self._episodes.generate(brief)
        from app.cli.project_generate import _semantic_song_inputs
        song_brief,music_plan=_semantic_song_inputs(episode,project_id)
        return self._projects.generate(episode,song_brief,music_plan,project_id,output_root,video_policy,music_policy,
                                       video_provider,music_provider)
