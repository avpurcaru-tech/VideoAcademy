import json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch

from app.web_ui.__main__ import main
from app.web_ui.bootstrap import (ApplicationSettings,AssetProviderSettings,LyricsProviderSettings,
    RuntimeMode,SecretValue,ServerSettings,SunoSettings,build_application_services)
from app.web_ui.server import create_app

class LocalProviderConfigurationTests(unittest.TestCase):
    def setUp(self): self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
    def tearDown(self): self.temp.cleanup()
    def config(self,value):
        path=self.root/"local.json"; path.write_text(json.dumps(value),encoding="utf-8"); return path
    def test_default_server_binds_to_loopback(self): self.assertEqual("127.0.0.1",ApplicationSettings.load(environ={}).server.host)
    def test_non_loopback_host_requires_explicit_override(self):
        with self.assertRaisesRegex(ValueError,"allow-non-loopback"): ApplicationSettings.load(environ={},cli={"host":"0.0.0.0"})
        self.assertEqual("0.0.0.0",ApplicationSettings.load(environ={},cli={"host":"0.0.0.0","allow_non_loopback":True}).server.host)
    def test_configuration_precedence_is_deterministic(self):
        path=self.config({"server":{"port":7000}}); self.assertEqual(7000,ApplicationSettings.load(path,environ={}).server.port)
    def test_environment_overrides_config_file(self):
        path=self.config({"server":{"port":7000}}); self.assertEqual(7001,ApplicationSettings.load(path,environ={"ACADEMIA_SERVER_PORT":"7001"}).server.port)
    def test_cli_overrides_environment(self): self.assertEqual(7002,ApplicationSettings.load(environ={"ACADEMIA_SERVER_PORT":"7001"},cli={"port":7002}).server.port)
    def test_secret_value_repr_is_redacted(self):
        secret=SecretValue("super-secret"); self.assertEqual("SecretValue(***)",repr(secret)); self.assertNotIn("super-secret",str(secret))
    def test_secret_value_is_not_serialized(self):
        with self.assertRaises(TypeError): json.dumps({"key":SecretValue("hidden")})
    def test_settings_page_never_exposes_api_keys(self):
        settings=ApplicationSettings(projects_root=self.root,lyrics=LyricsProviderSettings("openai",True,SecretValue("lyrics-secret"),"model"))
        body=create_app(settings=settings,services=build_application_services(settings=settings,runtime_mode="dry_run")).dispatch("/settings").body.decode()
        self.assertNotIn("lyrics-secret",body); self.assertNotIn("API key",body); self.assertIn("configured",body.casefold())
    def test_missing_optional_provider_is_reported_not_configured(self):
        settings=ApplicationSettings(projects_root=self.root); body=create_app(settings=settings,services=build_application_services(settings=settings,runtime_mode="production")).dispatch("/settings").body.decode()
        self.assertIn("Missing configuration",body)
    def test_enabled_provider_requires_required_fields(self):
        with self.assertRaisesRegex(ValueError,"lyrics provider"): ApplicationSettings(lyrics=LyricsProviderSettings("openai",True,None,"model"))
        with self.assertRaisesRegex(ValueError,"Suno"): ApplicationSettings(suno=SunoSettings(enabled=True))
        with self.assertRaisesRegex(ValueError,"asset provider"): ApplicationSettings(assets=AssetProviderSettings("kling",True,None,None))
    def test_dry_run_mode_allows_missing_real_credentials(self):
        settings=ApplicationSettings(runtime_mode="dry_run"); self.assertEqual(RuntimeMode.DRY_RUN,settings.runtime_mode); build_application_services(settings=settings,runtime_mode=settings.runtime_mode)
    def test_production_mode_reports_missing_required_credentials(self):
        settings=ApplicationSettings(runtime_mode="production"); labels=[x.label for x in build_application_services(settings=settings,runtime_mode=settings.runtime_mode).availability]; self.assertIn("Missing configuration",labels)
    def test_example_config_contains_no_real_secrets(self):
        text=(Path(__file__).parents[1]/"config"/"local.example.json").read_text(encoding="utf-8"); self.assertNotIn("api_key",text.casefold()); self.assertNotIn("token",text.casefold())
    def test_local_config_is_gitignored(self):
        text=(Path(__file__).parents[1]/".gitignore").read_text(encoding="utf-8"); self.assertIn("config/local.json",text); self.assertIn("*.local.json",text); self.assertIn(".env",text)
    def test_settings_loading_performs_zero_external_calls(self):
        with patch("requests.get") as get,patch("requests.post") as post,patch("subprocess.run") as run:
            ApplicationSettings.load(environ={}); get.assert_not_called(); post.assert_not_called(); run.assert_not_called()
    def test_cli_uses_loaded_host_port_and_settings(self):
        runner=Mock(); self.assertEqual(0,main(["--no-browser","--port","9090"],server_runner=runner)); self.assertEqual(("127.0.0.1",9090),runner.call_args.args[1:])
    def test_project_007_is_not_accessed(self):
        seven=self.root/"007"; seven.mkdir(); target=seven/"project.json"; target.write_bytes(b"protected"); settings=ApplicationSettings(projects_root=self.root); build_application_services(settings=settings,runtime_mode="dry_run"); self.assertEqual(b"protected",target.read_bytes())

if __name__=="__main__": unittest.main()
