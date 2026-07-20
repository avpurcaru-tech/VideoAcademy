import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch

from pydantic import ValidationError

from app.cli.song_generate_lyrics import main as cli_main
from app.providers.openai_lyrics_provider import (DEFAULT_OPENAI_LYRICS_MODEL, OpenAILyricsAPIError,
    OpenAILyricsAuthenticationError, OpenAILyricsConfigurationError, OpenAILyricsConnectionError, OpenAILyricsGenerator,
    OpenAILyricsRateLimitError, OpenAILyricsRefusalError, OpenAILyricsStructuredResponseError,
    OpenAILyricsTimeoutError, _LyricsResponseDTO, _build_input)
from app.song import DeterministicLyricsGenerator, LyricsGeneratorRegistry, LyricsPlan
from tests.test_song_planning import FIXTURES, brief


def dto(**updates):
    payload={"title":"Numărăm cu bucurie","sections":[
        {"section_id":"verse-1","kind":"verse","order":0,"lines":[{"line_id":"line-1","text":"Învățăm să numărăm."}]},
        {"section_id":"chorus-1","kind":"chorus","order":1,"lines":[{"line_id":"line-2","text":"Unu, doi, trei, hai și tu!"}]},
    ]}
    payload.update(updates); return _LyricsResponseDTO.model_validate(payload)


def client_with(parsed=None,output=()):
    client=Mock(); client.responses.parse.return_value=SimpleNamespace(output_parsed=parsed,output=output); return client


class OpenAILyricsProviderTests(unittest.TestCase):
    def test_valid_structured_response_maps_to_semantic_plan_and_calls_responses_once(self):
        client=client_with(dto()); generator=OpenAILyricsGenerator(client=client)
        result=generator.generate_lyrics(brief())
        self.assertIsInstance(result,LyricsPlan); self.assertEqual([s.kind.value for s in result.sections],["verse","chorus"])
        client.responses.parse.assert_called_once(); arguments=client.responses.parse.call_args.kwargs
        self.assertEqual(arguments["model"],DEFAULT_OPENAI_LYRICS_MODEL); self.assertIs(arguments["text_format"],_LyricsResponseDTO)

    def test_adapter_injects_brief_identity_and_preserves_language_unicode(self):
        result=OpenAILyricsGenerator(client=client_with(dto())).generate_lyrics(brief(song_id="durable-id",language="ro"))
        self.assertEqual((result.song_id,result.language),("durable-id","ro")); self.assertIn("Învățăm",result.to_json())

    def test_provider_dto_rejects_malformed_kinds_duplicates_empty_lines_and_order(self):
        cases=(
            {"title":"x","sections":[{"section_id":"v","kind":"refrain","order":0,"lines":[{"line_id":"l","text":"x"}]}]},
            {"title":"x","sections":[{"section_id":"same","kind":"verse","order":0,"lines":[{"line_id":"a","text":"x"}]},{"section_id":"same","kind":"chorus","order":1,"lines":[{"line_id":"b","text":"x"}]}]},
            {"title":"x","sections":[{"section_id":"v","kind":"verse","order":0,"lines":[{"line_id":"same","text":"x"}]},{"section_id":"c","kind":"chorus","order":1,"lines":[{"line_id":"same","text":"y"}]}]},
            {"title":"x","sections":[{"section_id":"v","kind":"verse","order":0,"lines":[{"line_id":"a","text":" "}]}]},
            {"title":"x","sections":[{"section_id":"v","kind":"verse","order":0,"lines":[{"line_id":"a","text":"x"}]},{"section_id":"c","kind":"chorus","order":0,"lines":[{"line_id":"b","text":"y"}]}]},
        )
        for payload in cases:
            with self.subTest(payload=payload),self.assertRaises(ValidationError): _LyricsResponseDTO.model_validate(payload)

    def test_missing_structured_output_and_refusal_are_distinct(self):
        with self.assertRaises(OpenAILyricsStructuredResponseError): OpenAILyricsGenerator(client=client_with()).generate_lyrics(brief())
        refusal=SimpleNamespace(type="message",content=(SimpleNamespace(type="refusal",refusal="raw refusal"),))
        with self.assertRaises(OpenAILyricsRefusalError) as raised:
            OpenAILyricsGenerator(client=client_with(dto(),(refusal,))).generate_lyrics(brief())
        self.assertNotIn("raw refusal",str(raised.exception))

    def test_sdk_validation_failure_is_malformed_structured_response(self):
        client=Mock()
        try: _LyricsResponseDTO.model_validate({"title":"","sections":[]})
        except ValidationError as error: client.responses.parse.side_effect=error
        with self.assertRaises(OpenAILyricsStructuredResponseError): OpenAILyricsGenerator(client=client).generate_lyrics(brief())

    def test_auth_rate_limit_timeout_and_api_failures_are_sanitized(self):
        mappings=(("AuthenticationError",OpenAILyricsAuthenticationError),("RateLimitError",OpenAILyricsRateLimitError),
                  ("APITimeoutError",OpenAILyricsTimeoutError),("APIConnectionError",OpenAILyricsConnectionError),
                  ("APIError",OpenAILyricsAPIError))
        for global_name,expected in mappings:
            raw_type=type(f"Fake{global_name}",(Exception,),{}); client=Mock()
            client.responses.parse.side_effect=raw_type("API_KEY Authorization full prompt raw response")
            with self.subTest(global_name=global_name),patch(f"app.providers.openai_lyrics_provider.{global_name}",raw_type), \
                 self.assertRaises(expected) as raised: OpenAILyricsGenerator(client=client).generate_lyrics(brief())
            for secret in ("API_KEY","Authorization","full prompt","raw response"): self.assertNotIn(secret,str(raised.exception))

    def test_configuration_requires_key_only_without_injected_client_and_disables_sdk_retries(self):
        with patch.dict("os.environ",{},clear=True),self.assertRaises(OpenAILyricsConfigurationError): OpenAILyricsGenerator()
        with patch("app.providers.openai_lyrics_provider.OpenAI") as sdk,patch.dict("os.environ",{"OPENAI_API_KEY":"secret"},clear=True):
            OpenAILyricsGenerator(); sdk.assert_called_once_with(api_key="secret",max_retries=0)

    def test_prompt_contains_semantic_requirements_but_no_artist_imitation(self):
        prompt="\n".join(item["content"] for item in _build_input(brief())).lower()
        for expected in ("original","simple vocabulary","short singable lines","repetition","learning objective","verse","chorus",
                         "target age","language","duration","tone"):
            self.assertIn(expected,prompt)
        self.assertIn("never imitate",prompt); self.assertNotIn("living artist",prompt)

    def test_registry_deterministic_needs_no_key_and_openai_is_lazy(self):
        with patch.dict("os.environ",{},clear=True): self.assertIsInstance(LyricsGeneratorRegistry().resolve("deterministic"),DeterministicLyricsGenerator)
        fake=client_with(dto())
        with patch("app.providers.openai_lyrics_provider.OpenAI",return_value=fake) as sdk, \
             patch.dict("os.environ",{"OPENAI_API_KEY":"configured"},clear=True):
            selected=LyricsGeneratorRegistry().resolve("openai")
        self.assertIsInstance(selected,OpenAILyricsGenerator); sdk.assert_called_once_with(api_key="configured",max_retries=0)

    def test_cli_openai_without_confirm_validates_brief_but_constructs_no_generator(self):
        with tempfile.TemporaryDirectory() as directory:
            argv=["song_generate_lyrics","--brief",str(FIXTURES/"song-brief.json"),"--generator","openai",
                  "--output",str(Path(directory)/"lyrics.json")]
            with patch("sys.argv",argv),patch("app.cli.song_generate_lyrics.LyricsGeneratorRegistry") as registry,patch("builtins.print") as emit:
                self.assertEqual(cli_main(),2)
        registry.assert_not_called(); output=" ".join(str(call.args[0]) for call in emit.call_args_list)
        self.assertIn("may incur API costs",output); self.assertNotIn("OPENAI_API_KEY",output)

    def test_cli_confirm_delegates_once_persists_atomically_and_hides_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            destination=Path(directory)/"lyrics.json"
            class Generator:
                def __init__(self): self.calls=0
                def generate_lyrics(self,value):
                    self.calls+=1; return OpenAILyricsGenerator(client=client_with(dto())).generate_lyrics(value)
            generator=Generator()
            registry=Mock(); registry.resolve.return_value=generator
            argv=["song_generate_lyrics","--brief",str(FIXTURES/"song-brief.json"),"--generator","openai",
                  "--output",str(destination),"--confirm"]
            with patch("sys.argv",argv),patch("app.cli.song_generate_lyrics.LyricsGeneratorRegistry",return_value=registry),patch("builtins.print") as emit:
                self.assertEqual(cli_main(),0)
            self.assertEqual(generator.calls,1); self.assertTrue(destination.is_file()); self.assertFalse(destination.with_suffix(".json.part").exists())
            persisted=json.loads(destination.read_text(encoding="utf-8")); self.assertEqual(persisted["language"],"ro")
        output=" ".join(str(call.args[0]) for call in emit.call_args_list)
        for forbidden in ("API_KEY","Authorization","original educational song","raw response"): self.assertNotIn(forbidden,output)


if __name__=="__main__": unittest.main()
