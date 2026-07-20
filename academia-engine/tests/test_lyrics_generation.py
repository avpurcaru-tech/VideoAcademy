import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.cli.song_generate_lyrics import main as cli_main
from app.song import (DeterministicLyricsGenerator, GeneratedLyricsLanguageMismatchError,
                      GeneratedLyricsSongIdMismatchError, InvalidGeneratedLyricsError, LyricsGenerationService,
                      LyricsGenerator, LyricsGeneratorFailureError, LyricsGeneratorRegistry,
                      LyricsOutputConflictError, LyricsPlan, UnsupportedLyricsGeneratorError,
                      persist_lyrics_atomic)
from tests.test_song_planning import FIXTURES, brief, lyrics


class StubGenerator:
    def __init__(self,result=None,error=None): self.result=result; self.error=error; self.calls=0; self.received=None
    def generate_lyrics(self,song_brief):
        self.calls+=1; self.received=song_brief
        if self.error: raise self.error
        return self.result


class LyricsGenerationTests(unittest.TestCase):
    def test_protocol_is_provider_neutral_and_structural(self):
        self.assertIsInstance(DeterministicLyricsGenerator(),LyricsGenerator)
        self.assertIsInstance(StubGenerator(lyrics()),LyricsGenerator)

    def test_service_calls_generator_once_and_accepts_valid_lyrics(self):
        expected=lyrics(); generator=StubGenerator(expected)
        actual=LyricsGenerationService(generator).generate(brief())
        self.assertEqual(actual,expected); self.assertEqual(generator.calls,1); self.assertEqual(generator.received,brief())

    def test_song_id_and_language_mismatches_are_distinct(self):
        cases=((lyrics(song_id="other"),GeneratedLyricsSongIdMismatchError),
               (lyrics(language="en"),GeneratedLyricsLanguageMismatchError))
        for generated,error_type in cases:
            generator=StubGenerator(generated)
            with self.subTest(error=error_type),self.assertRaises(error_type): LyricsGenerationService(generator).generate(brief())
            self.assertEqual(generator.calls,1)

    def test_invalid_generated_contract_is_rejected(self):
        generator=StubGenerator({"song_id":"counting-1-to-5","language":"ro","sections":[]})
        with self.assertRaises(InvalidGeneratedLyricsError): LyricsGenerationService(generator).generate(brief())
        self.assertEqual(generator.calls,1)

    def test_generator_failure_is_wrapped_without_raw_message(self):
        generator=StubGenerator(error=RuntimeError("API_KEY raw provider response"))
        with self.assertRaises(LyricsGeneratorFailureError) as raised: LyricsGenerationService(generator).generate(brief())
        self.assertNotIn("API_KEY",str(raised.exception)); self.assertEqual(generator.calls,1)

    def test_deterministic_generator_has_stable_valid_output(self):
        generator=DeterministicLyricsGenerator(); first=generator.generate_lyrics(brief()); second=generator.generate_lyrics(brief())
        self.assertEqual(first,second); self.assertEqual(first.to_json(),second.to_json())
        self.assertEqual([section.kind.value for section in first.sections],["verse","chorus"])

    def test_deterministic_generator_supports_arbitrary_valid_brief(self):
        other=brief(song_id="shapes",topic="Basic shapes",learning_objectives=("Recognize a circle",),language="en")
        generated=LyricsGenerationService(DeterministicLyricsGenerator()).generate(other)
        self.assertEqual((generated.song_id,generated.language),("shapes","en")); self.assertIn("Basic shapes",generated.title)

    def test_registry_resolves_known_and_rejects_unknown_or_invalid(self):
        self.assertIsInstance(LyricsGeneratorRegistry().resolve("deterministic"),DeterministicLyricsGenerator)
        for registry,name in ((LyricsGeneratorRegistry(),"unknown"),(LyricsGeneratorRegistry({"invalid":object()}),"invalid")):
            with self.subTest(name=name),self.assertRaises(UnsupportedLyricsGeneratorError): registry.resolve(name)

    def test_atomic_persistence_round_trip_and_no_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            destination=Path(directory)/"nested"/"lyrics.json"; result=persist_lyrics_atomic(lyrics(),destination)
            self.assertEqual(result,destination); self.assertFalse(destination.with_suffix(".json.part").exists())
            self.assertEqual(LyricsPlan.model_validate_json(destination.read_text(encoding="utf-8")),lyrics())

    def test_existing_output_rejected_and_overwrite_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            destination=Path(directory)/"lyrics.json"; destination.write_text("original",encoding="utf-8")
            with self.assertRaises(LyricsOutputConflictError): persist_lyrics_atomic(lyrics(),destination)
            self.assertEqual(destination.read_text(encoding="utf-8"),"original")
            persist_lyrics_atomic(lyrics(title="Titlu nou"),destination,overwrite=True)
            self.assertEqual(LyricsPlan.model_validate_json(destination.read_text(encoding="utf-8")).title,"Titlu nou")

    def test_atomic_replace_is_used_after_fsync_path(self):
        with tempfile.TemporaryDirectory() as directory:
            destination=Path(directory)/"lyrics.json"
            with patch("app.song.lyrics_writer.os.replace",wraps=__import__("os").replace) as replace:
                persist_lyrics_atomic(lyrics(),destination)
            replace.assert_called_once_with(destination.with_suffix(".json.part"),destination)

    def test_cli_success_hides_lines_without_show_and_preserves_safe_json(self):
        with tempfile.TemporaryDirectory() as directory:
            destination=Path(directory)/"songs"/"lyrics.json"
            argv=["song_generate_lyrics","--brief",str(FIXTURES/"song-brief.json"),"--generator","deterministic","--output",str(destination)]
            with patch("sys.argv",argv),patch("builtins.print") as emit: self.assertEqual(cli_main(),0)
            output="\n".join(str(call.args[0]) for call in emit.call_args_list)
            self.assertIn("Song ID: counting-1-to-5",output); self.assertIn("Saved path:",output)
            self.assertNotIn("Învățăm astăzi",output)
            raw=destination.read_text(encoding="utf-8"); self.assertIn("Cântec",raw)
            for forbidden in ("api_key","authorization","provider_payload","raw_response","hidden_prompt","http://","https://"):
                self.assertNotIn(forbidden,raw.lower())

    def test_cli_show_prints_lyrics_and_overwrite_flag_controls_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            destination=Path(directory)/"lyrics.json"
            base=["song_generate_lyrics","--brief",str(FIXTURES/"song-brief.json"),"--output",str(destination)]
            with patch("sys.argv",base),patch("builtins.print"): self.assertEqual(cli_main(),0)
            with patch("sys.argv",base+["--show"]),patch("builtins.print"): self.assertEqual(cli_main(),1)
            with patch("sys.argv",base+["--overwrite","--show"]),patch("builtins.print") as emit: self.assertEqual(cli_main(),0)
            output="\n".join(str(call.args[0]) for call in emit.call_args_list)
            self.assertIn("Verse:",output); self.assertIn("Chorus:",output); self.assertIn("Învățăm astăzi",output)


if __name__=="__main__": unittest.main()
