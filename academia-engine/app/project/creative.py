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
        if self._registry.exists(project_id):
            from .contracts import ProjectStatus
            if self._registry.load(project_id).status != ProjectStatus.PLANNED:
                raise RuntimeError("Project already exists; use resume.")
        else:
            from .orchestrator import ProjectGenerationService
            ProjectGenerationService.create_planned(self._registry,project_id,output_root,brief.brief_id,brief.series_id)
        try:
            episode=self._derive_episode(brief)
            from app.cli.project_generate import _semantic_song_inputs
            song_brief,music_plan=_semantic_song_inputs(episode,project_id)
        except Exception as error:
            self._persist_early_failure(project_id,error)
            raise
        return self._projects.generate(episode,song_brief,music_plan,project_id,output_root,video_policy,music_policy,
                                       video_provider,music_provider,series_id=brief.series_id)
    def _persist_early_failure(self,project_id,error):
        from app.characters import CharacterRegistryError
        from app.series import SeriesRegistryError
        from app.storyboard import StoryboardGenerationError,StoryboardPersistenceError
        from .contracts import ProjectFailureStage
        from .orchestrator import ProjectGenerationService
        if isinstance(error,CharacterRegistryError):
            values=(ProjectFailureStage.CHARACTER_RESOLUTION,"character_resolution_failed","Character resolution failed at a safe boundary.")
        elif isinstance(error,SeriesRegistryError):
            values=(ProjectFailureStage.SERIES_RESOLUTION,"series_resolution_failed","Series resolution failed at a safe boundary.")
        elif isinstance(error,(StoryboardGenerationError,StoryboardPersistenceError)):
            category,details,provider=self._storyboard_failure(error)
            record=ProjectGenerationService.fail_planned(self._registry,project_id,ProjectFailureStage.STORYBOARD_GENERATION,
                category,"Storyboard generation failed at a safe boundary.")
            updates={"failure_details":details}
            for source,target in (("http_status","provider_http_status"),("request_id","provider_request_id"),
                                  ("model","provider_model"),("retry_after","provider_retry_after")):
                updates[target]=getattr(provider,source,None)
            self._registry.update(record.model_copy(update=updates))
            return
        else:
            values=(ProjectFailureStage.EPISODE_GENERATION,"episode_generation_failed","Episode generation failed at a safe boundary.")
        ProjectGenerationService.fail_planned(self._registry,project_id,*values)
    @staticmethod
    def _storyboard_failure(error):
        from app.storyboard import StoryboardPersistenceError
        from app.providers.openai_storyboard_provider import (OpenAIStoryboardAPIError,OpenAIStoryboardAuthenticationError,
            OpenAIStoryboardConfigurationError,OpenAIStoryboardConnectionError,OpenAIStoryboardRateLimitError,
            OpenAIStoryboardRefusalError,OpenAIStoryboardStructuredOutputMalformedError,
            OpenAIStoryboardStructuredOutputMissingError,OpenAIStoryboardTimeoutError,OpenAIStoryboardUnavailableError)
        provider=getattr(error,"provider_error",None) or error.__cause__ or error
        mappings=((OpenAIStoryboardConfigurationError,"storyboard_provider_configuration_missing"),
            (OpenAIStoryboardUnavailableError,"storyboard_provider_unavailable"),
            (OpenAIStoryboardAuthenticationError,"storyboard_authentication_failed"),
            (OpenAIStoryboardRateLimitError,"storyboard_rate_limited"),(OpenAIStoryboardTimeoutError,"storyboard_timeout"),
            (OpenAIStoryboardConnectionError,"storyboard_network_failed"),(OpenAIStoryboardRefusalError,"storyboard_refused"),
            (OpenAIStoryboardStructuredOutputMalformedError,"storyboard_structured_output_malformed"),
            (OpenAIStoryboardStructuredOutputMissingError,"storyboard_structured_output_missing"),
            (OpenAIStoryboardAPIError,"storyboard_api_failed"),(StoryboardPersistenceError,"storyboard_persistence_failed"))
        category=getattr(error,"failure_category",None)
        if category is None:
            category=next((value for kind,value in mappings if isinstance(provider,kind)),"storyboard_validation_failed")
        return category,tuple(getattr(error,"failure_details",())),provider
