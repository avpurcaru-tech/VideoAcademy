import importlib,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch

from app.web_ui.assets import AssetGenerationRequest
from app.web_ui.bootstrap import *
from app.web_ui.composition import ExistingFFmpegCompositionAdapter
from app.web_ui.lyrics import LyricsGenerationRequest
from app.web_ui.music import MusicGenerationRequest
from app.web_ui.server import create_app,create_application

class ProductionAdapterWiringTests(unittest.TestCase):
    def setUp(self): self.temporary=tempfile.TemporaryDirectory(); self.root=Path(self.temporary.name); self.settings=ApplicationSettings(projects_root=self.root)
    def tearDown(self): self.temporary.cleanup()
    def test_application_services_are_built_in_one_composition_root(self):
        services=build_application_services(settings=self.settings,runtime_mode="test"); self.assertIsInstance(services,ApplicationServices); self.assertEqual(RuntimeMode.TEST,services.runtime_mode)
    def test_test_mode_uses_fake_providers(self):
        services=build_application_services(settings=self.settings,runtime_mode="test"); self.assertIsInstance(services.lyrics_provider,DeterministicLyricsProvider); self.assertIsInstance(services.music_provider,DeterministicMusicProvider); self.assertIsInstance(services.asset_provider,DeterministicAssetProvider)
    def test_dry_run_mode_does_not_send_external_requests(self):
        with patch("requests.post") as post,patch("requests.get") as get:
            services=build_application_services(settings=self.settings,runtime_mode="dry_run")
            result=services.lyrics_provider.generate(LyricsGenerationRequest(episode_title="Titlu",description="Descriere",language="ro",target_age="2-5",main_character_name="Luca",main_character_description="Copil"))
            self.assertIn("Luca",result.lyrics_text); post.assert_not_called(); get.assert_not_called()
    def test_production_mode_uses_configured_music_adapter(self):
        settings=ApplicationSettings(projects_root=self.root,suno_api_key="configured",suno_callback_url="https://example.test/callback")
        services=build_application_services(settings=settings,runtime_mode="production"); self.assertIsInstance(services.music_provider,LazySunoMusicUiAdapter)
    def test_missing_optional_provider_does_not_crash_application_startup(self):
        services=build_application_services(settings=self.settings,runtime_mode="production"); app=create_app(settings=self.settings,services=services); self.assertIsInstance(services.asset_provider,DisabledProvider); self.assertEqual(200,app.dispatch("/").status)
    def test_ui_server_receives_injected_services(self):
        services=build_application_services(settings=self.settings,runtime_mode="test"); app=create_app(settings=self.settings,services=services); self.assertIs(services,app.services); self.assertIs(services.music_provider,app.music_provider)
    def test_importing_server_does_not_create_external_clients(self):
        import app.web_ui.server as server
        with patch("requests.Session") as session,patch("openai.OpenAI") as openai: importlib.reload(server); session.assert_not_called(); openai.assert_not_called()
    def test_opening_index_does_not_call_any_provider(self):
        services=self._mock_services(); app=create_app(settings=self.settings,services=services); app.dispatch("/"); self._providers_not_called(services)
    def test_opening_project_page_does_not_call_any_provider(self):
        project=self.root/"008"; project.mkdir(); (project/"project.json").write_text("{}",encoding="utf-8"); services=self._mock_services(); create_app(settings=self.settings,services=services).dispatch("/projects/008"); self._providers_not_called(services)
    def test_health_route_does_not_call_any_provider(self):
        services=self._mock_services(); self.assertEqual(200,create_app(settings=self.settings,services=services).dispatch("/health").status); self._providers_not_called(services)
    def test_music_action_uses_injected_music_provider(self):
        services=self._mock_services(); app=create_app(settings=self.settings,services=services); self.assertIs(services.music_provider,app.music_provider)
    def test_alignment_action_uses_existing_alignment_service(self):
        services=build_application_services(settings=self.settings,runtime_mode="production"); self.assertIsInstance(services.planning_builders["alignment"],ExistingAlignmentUiBuilder)
    def test_scene_plan_action_uses_existing_scene_planner(self):
        services=build_application_services(settings=self.settings,runtime_mode="production"); self.assertIsInstance(services.planning_builders["scene_plan"],ExistingScenePlanUiBuilder)
    def test_visual_plan_action_uses_existing_visual_planner(self):
        services=build_application_services(settings=self.settings,runtime_mode="production"); self.assertIsInstance(services.planning_builders["visual_plan"],ExistingVisualPlanUiBuilder)
    def test_prompt_action_uses_existing_prompt_builder(self):
        services=build_application_services(settings=self.settings,runtime_mode="production"); self.assertIsInstance(services.planning_builders["prompts"],ExistingPromptUiBuilder)
    def test_asset_action_uses_injected_asset_provider(self):
        services=self._mock_services(); self.assertIs(services.asset_provider,create_app(settings=self.settings,services=services).asset_provider)
    def test_composition_action_uses_existing_ffmpeg_adapter(self):
        services=build_application_services(settings=self.settings,runtime_mode="production"); self.assertIsInstance(services.composition_renderer,ExistingFFmpegCompositionAdapter)
    def test_preflight_does_not_execute_ffmpeg(self):
        with patch("app.media.process_runner.SubprocessProcessRunner.run") as execute: build_application_services(settings=self.settings,runtime_mode="production"); execute.assert_not_called()
    def test_production_wiring_preserves_manual_approval_gates(self):
        services=build_application_services(settings=self.settings,runtime_mode="production"); app=create_app(settings=self.settings,services=services); self.assertEqual(0,len(tuple(self.root.glob("**/version-*")))); self.assertEqual(200,app.dispatch("/").status)
    def test_project_007_is_not_accessed(self):
        seven=self.root/"007"; seven.mkdir(); target=seven/"project.json"; target.write_bytes(b"protected"); before=target.read_bytes(); build_application_services(settings=self.settings,runtime_mode="production"); self.assertEqual(before,target.read_bytes())
    def test_adapter_wiring_tests_make_zero_real_external_calls(self):
        with patch("requests.post") as post,patch("requests.get") as get,patch("subprocess.run") as run:
            build_application_services(settings=self.settings,runtime_mode="test"); build_application_services(settings=self.settings,runtime_mode="dry_run"); build_application_services(settings=self.settings,runtime_mode="production")
            post.assert_not_called(); get.assert_not_called(); run.assert_not_called()
    def _mock_services(self):
        return ApplicationServices(Mock(),Mock(),{},Mock(),Mock(),(ProviderAvailability("all",True,True),),RuntimeMode.TEST)
    def _providers_not_called(self,services):
        services.lyrics_provider.assert_not_called(); services.music_provider.assert_not_called(); services.asset_provider.assert_not_called(); services.composition_renderer.assert_not_called()

if __name__=="__main__": unittest.main()
