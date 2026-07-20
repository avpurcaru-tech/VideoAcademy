import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

from app.cli.music_generate import main as music_main
from app.cli.song_generate_lyrics import main as lyrics_main
from app.config.environment import application_root,load_application_environment
from app.providers.openai_lyrics_provider import OpenAILyricsGenerator
from app.providers.sunoapi_org_music_provider import SunoApiOrgMusicProvider
from tests.test_song_planning import FIXTURES


class EnvironmentBootstrapTests(unittest.TestCase):
    KEYS=("OPENAI_API_KEY","OPENAI_LYRICS_MODEL","SUNOAPI_ORG_API_KEY","SUNOAPI_ORG_CALLBACK_URL","SUNOAPI_ORG_MODEL")
    def setUp(self): self.saved={key:os.environ.get(key) for key in self.KEYS}
    def tearDown(self):
        for key,value in self.saved.items():
            if value is None: os.environ.pop(key,None)
            else: os.environ[key]=value

    def clear(self):
        for key in self.KEYS: os.environ.pop(key,None)

    def test_default_resolution_loads_parent_videoacademy_env_independent_of_cwd(self):
        self.clear()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/"VideoAcademy"; root.mkdir(); (root/".env").write_text("OPENAI_API_KEY=from-videoacademy-root\n",encoding="utf-8")
            with patch("app.config.environment.application_root",return_value=root),patch("os.getcwd",return_value="C:\\unrelated"):
                self.assertTrue(load_application_environment())
        self.assertEqual(os.environ["OPENAI_API_KEY"],"from-videoacademy-root")
        self.assertEqual(application_root(),Path(__file__).resolve().parents[2])

    def test_expected_shared_environment_path_is_parent_of_code_project(self):
        code_root=Path(__file__).resolve().parents[1]
        self.assertEqual(application_root(),code_root.parent)
        self.assertEqual(application_root()/".env",code_root.parent/".env")

    def test_process_environment_precedes_dotenv(self):
        self.clear(); os.environ["OPENAI_API_KEY"]="process-value"
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/".env"; path.write_text("OPENAI_API_KEY=file-value\n",encoding="utf-8")
            load_application_environment(path)
        self.assertEqual(os.environ["OPENAI_API_KEY"],"process-value")

    def test_openai_and_gateway_provider_construction_see_loaded_values(self):
        self.clear()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/".env"; path.write_text(
                "OPENAI_API_KEY=openai-from-env\nOPENAI_LYRICS_MODEL=gpt-test\n"
                "SUNOAPI_ORG_API_KEY=gateway-from-env\nSUNOAPI_ORG_CALLBACK_URL=https://callback.invalid/music\nSUNOAPI_ORG_MODEL=V4_5\n",encoding="utf-8")
            load_application_environment(path)
        with patch("app.providers.openai_lyrics_provider.OpenAI",return_value=Mock()) as client:
            OpenAILyricsGenerator()
        self.assertEqual(client.call_args.kwargs["api_key"],"openai-from-env")
        provider=SunoApiOrgMusicProvider.from_environment()
        self.assertEqual(provider._transport._key,"gateway-from-env")

    def test_missing_env_is_safe_and_deterministic_generator_still_works(self):
        self.clear()
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(load_application_environment(Path(directory)/"missing.env"))
            output=Path(directory)/"lyrics.json"; argv=["song_generate_lyrics","--brief",str(FIXTURES/"song-brief.json"),"--generator","deterministic","--output",str(output)]
            with patch("sys.argv",argv),patch("app.cli.song_generate_lyrics.load_application_environment",return_value=False),patch("builtins.print"):
                self.assertEqual(lyrics_main(),0)
            self.assertTrue(output.is_file())

    def test_openai_missing_configuration_is_not_reported_as_unsupported(self):
        self.clear()
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/"lyrics.json"; argv=["song_generate_lyrics","--brief",str(FIXTURES/"song-brief.json"),"--generator","openai","--output",str(output),"--confirm"]
            with patch("sys.argv",argv),patch("app.cli.song_generate_lyrics.load_application_environment",return_value=False),patch("builtins.print") as emit:
                self.assertEqual(lyrics_main(),1)
        text="\n".join(str(call.args[0]) for call in emit.call_args_list)
        self.assertIn("OpenAI lyrics provider configuration is missing.",text); self.assertNotIn("unsupported",text)

    def test_malformed_env_leads_to_sanitized_missing_configuration(self):
        self.clear()
        with tempfile.TemporaryDirectory() as directory:
            env_path=Path(directory)/".env"; env_path.write_text("OPENAI_API_KEY='UNTERMINATED_SECRET\n",encoding="utf-8")
            load_application_environment(env_path)
            output=Path(directory)/"lyrics.json"; argv=["song_generate_lyrics","--brief",str(FIXTURES/"song-brief.json"),"--generator","openai","--output",str(output),"--confirm"]
            with patch("sys.argv",argv),patch("app.cli.song_generate_lyrics.load_application_environment",return_value=False),patch("builtins.print") as emit:
                self.assertEqual(lyrics_main(),1)
        text="\n".join(str(call.args[0]) for call in emit.call_args_list)
        self.assertEqual(text,"OpenAI lyrics provider configuration is missing."); self.assertNotIn("UNTERMINATED_SECRET",text)

    def test_unknown_and_unavailable_generator_diagnostics_are_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            base=["song_generate_lyrics","--brief",str(FIXTURES/"song-brief.json"),"--output",str(Path(directory)/"out.json")]
            with patch("sys.argv",base+["--generator","unknown"]),patch("app.cli.song_generate_lyrics.load_application_environment"),patch("builtins.print") as emit:
                self.assertEqual(lyrics_main(),1)
            self.assertIn("Lyrics generator is unsupported."," ".join(str(c.args[0]) for c in emit.call_args_list))
            from app.song import LyricsGeneratorUnavailableError
            with patch("sys.argv",base+["--generator","openai","--confirm"]),patch("app.cli.song_generate_lyrics.load_application_environment"),patch("app.cli.song_generate_lyrics.LyricsGeneratorRegistry.resolve",side_effect=LyricsGeneratorUnavailableError()),patch("builtins.print") as emit:
                self.assertEqual(lyrics_main(),1)
            self.assertIn("OpenAI lyrics provider is unavailable."," ".join(str(c.args[0]) for c in emit.call_args_list))

    def test_gateway_missing_configuration_and_secret_output_are_sanitized(self):
        self.clear(); root=Path(__file__).resolve().parents[1]/"examples"/"smoke"
        argv=["music_generate","--lyrics",str(root/"lyrics-plan.json"),"--music-plan",str(root/"music-plan.json"),"--provider","sunoapi_org","--output-dir","songs","--download-all","--confirm"]
        with patch("sys.argv",argv),patch("app.cli.music_generate.load_application_environment",return_value=False),patch("builtins.print") as emit:
            self.assertEqual(music_main(),1)
        text="\n".join(str(call.args[0]) for call in emit.call_args_list)
        self.assertIn("Third-party music provider configuration is missing.",text)
        for forbidden in ("Authorization","Bearer","SUNOAPI_ORG_API_KEY"):
            self.assertNotIn(forbidden,text)


if __name__=="__main__": unittest.main()
