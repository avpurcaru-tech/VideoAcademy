import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from app.cli.song_show import main as show_main
from app.cli.song_validate import main as validate_main
from app.song import (EducationalSongBrief, LyricsLine, LyricsPlan, LyricsSection, LyricsSectionKind,
                      MusicPlan, SONG_DURATION_TOLERANCE_SECONDS, SongPlanner, SongProductionPlan,
                      resolve_lyrics)


ROOT=Path(__file__).resolve().parents[1]
FIXTURES=ROOT/"examples"/"smoke"


def brief(**updates):
    values=dict(song_id="counting-1-to-5",topic="Numărarea de la 1 la 5",
                learning_objectives=("Recunoaște numerele",),language="ro",target_age_min=3,target_age_max=5,
                target_duration_seconds=75,tone="vesel",repetition_level="ridicat")
    values.update(updates); return EducationalSongBrief(**values)


def section(section_id,kind,order,line_id,text="Numărăm împreună"):
    return LyricsSection(section_id=section_id,kind=kind,order=order,lines=(LyricsLine(line_id=line_id,text=text),))


def lyrics(**updates):
    values=dict(song_id="counting-1-to-5",title="Cântecul numerelor",language="ro",sections=(
        section("verse-1","verse",0,"line-1","Unu, doi, pornim voioși"),
        section("chorus-1","chorus",1,"line-2","Numărăm până la cinci")))
    values.update(updates); return LyricsPlan(**values)


def music(**updates):
    values=dict(song_id="counting-1-to-5",tempo_bpm=112,musical_style="pop acustic",mood="jucăuș",
                instrumentation=("ukulele","xilofon"),vocal_style="voce caldă",target_duration_seconds=75)
    values.update(updates); return MusicPlan(**values)


class SongContractTests(unittest.TestCase):
    def test_valid_brief_and_age_boundaries(self):
        value=brief(); self.assertEqual((value.target_age_min,value.target_age_max),(3,5))
        with self.assertRaises(ValidationError): brief(target_age_min=6,target_age_max=5)

    def test_brief_rejects_invalid_duration_topic_objectives_and_nulls(self):
        for updates in ({"target_duration_seconds":0},{"target_duration_seconds":float("inf")},
                        {"topic":"   "},{"learning_objectives":()},{"tone":"safe\0hidden"}):
            with self.subTest(updates=updates),self.assertRaises(ValidationError): brief(**updates)

    def test_valid_lyrics_requires_verse_and_chorus(self):
        self.assertEqual(len(lyrics().sections),2)
        for sections in ((section("verse","verse",0,"one"),),(section("chorus","chorus",0,"one"),)):
            with self.subTest(sections=sections),self.assertRaises(ValidationError): lyrics(sections=sections)

    def test_lyrics_rejects_duplicate_section_ids_orders_and_song_wide_line_ids(self):
        cases=((section("same","verse",0,"one"),section("same","chorus",1,"two")),
               (section("verse","verse",0,"one"),section("chorus","chorus",0,"two")),
               (section("verse","verse",0,"same"),section("chorus","chorus",1,"same")))
        for sections in cases:
            with self.subTest(sections=sections),self.assertRaises(ValidationError): lyrics(sections=sections)

    def test_section_order_is_deterministic_and_repeated_choruses_are_allowed(self):
        sections=(section("chorus-2","chorus",3,"four"),section("verse","verse",0,"one"),
                  section("chorus-1","chorus",1,"two"),section("outro","outro",2,"three"))
        value=lyrics(sections=sections)
        self.assertEqual([item.order for item in value.sections],[0,1,2,3])
        self.assertEqual(sum(item.kind==LyricsSectionKind.CHORUS for item in value.sections),2)

    def test_unicode_and_romanian_diacritics_are_preserved_exactly(self):
        text="Ăă Ââ Îî Șș Țț — numărăm împreună!"
        value=lyrics(sections=(section("verse","verse",0,"one",text),section("chorus","chorus",1,"two")))
        self.assertEqual(value.sections[0].lines[0].text,text); self.assertIn(text,value.to_json())

    def test_lines_reject_blank_and_null_text(self):
        for value in (" ","cântec\0secret"):
            with self.assertRaises(ValidationError): LyricsLine(line_id="line",text=value)

    def test_valid_music_and_invalid_tempo_or_instrumentation(self):
        self.assertEqual(music().tempo_bpm,112)
        for updates in ({"tempo_bpm":0},{"tempo_bpm":float("nan")},{"instrumentation":()},
                        {"instrumentation":(" ",)}):
            with self.subTest(updates=updates),self.assertRaises(ValidationError): music(**updates)

    def test_production_plan_consistency_and_named_duration_tolerance(self):
        value=SongPlanner().plan(brief(),lyrics(),music(target_duration_seconds=75+SONG_DURATION_TOLERANCE_SECONDS))
        self.assertIsInstance(value,SongProductionPlan)
        cases=((brief(song_id="different"),lyrics(),music()),(brief(),lyrics(language="en"),music()),
               (brief(),lyrics(),music(target_duration_seconds=77)))
        for components in cases:
            with self.subTest(components=components),self.assertRaises(ValidationError): SongPlanner().plan(*components)

    def test_deterministic_serialization_and_round_trip(self):
        value=SongPlanner().plan(brief(),lyrics(),music()); first=value.to_json(); second=value.to_json()
        self.assertEqual(first,second); self.assertEqual(SongProductionPlan.from_json(first),value)
        self.assertFalse(json.loads(first).get("provider")); self.assertNotIn("prompt",first.lower())

    def test_resolver_does_not_mutate_lyrics_or_line_order(self):
        value=lyrics(); before=value.to_json(); resolved=resolve_lyrics(value)
        self.assertEqual(value.to_json(),before); self.assertEqual(resolved.structural_order,("verse-1","chorus-1"))
        self.assertEqual(resolved.sections[0].lines,value.sections[0].lines)

    def test_smoke_fixtures_deserialize_and_plan(self):
        loaded_brief=EducationalSongBrief.model_validate_json((FIXTURES/"song-brief.json").read_text(encoding="utf-8"))
        loaded_lyrics=LyricsPlan.model_validate_json((FIXTURES/"lyrics-plan.json").read_text(encoding="utf-8"))
        loaded_music=MusicPlan.model_validate_json((FIXTURES/"music-plan.json").read_text(encoding="utf-8"))
        self.assertEqual(SongPlanner().plan(loaded_brief,loaded_lyrics,loaded_music).brief.song_id,"counting-1-to-5")
        combined=" ".join((FIXTURES/name).read_text(encoding="utf-8").lower()
                          for name in ("song-brief.json","lyrics-plan.json","music-plan.json"))
        for forbidden in ("api_key","authorization","signed_url","provider_payload","model_version","http://","https://"):
            self.assertNotIn(forbidden,combined)

    def test_validation_cli_is_sanitized_and_successful(self):
        argv=["song_validate","--brief",str(FIXTURES/"song-brief.json"),"--lyrics",str(FIXTURES/"lyrics-plan.json"),
              "--music",str(FIXTURES/"music-plan.json")]
        with patch("sys.argv",argv),patch("builtins.print") as emit: self.assertEqual(validate_main(),0)
        output="\n".join(str(call.args[0]) for call in emit.call_args_list)
        for expected in ("Song ID: counting-1-to-5","Language: ro","Sections: 4","Validation: passed"): self.assertIn(expected,output)
        for forbidden in ("Authorization","api_key","signed URL","provider payload","prompt"): self.assertNotIn(forbidden,output)

    def test_validation_cli_does_not_echo_invalid_sensitive_values(self):
        with tempfile.TemporaryDirectory() as directory:
            invalid=Path(directory)/"brief.json"
            payload=json.loads((FIXTURES/"song-brief.json").read_text(encoding="utf-8")); payload["topic"]="SECRET_TOKEN\0"
            invalid.write_text(json.dumps(payload),encoding="utf-8")
            with patch("sys.argv",["song_validate","--brief",str(invalid),"--lyrics",str(FIXTURES/"lyrics-plan.json"),"--music",str(FIXTURES/"music-plan.json")]), \
                 patch("builtins.print") as emit: self.assertEqual(validate_main(),1)
        self.assertNotIn("SECRET_TOKEN"," ".join(str(call.args[0]) for call in emit.call_args_list))

    def test_song_show_prints_user_visible_lyrics_in_order(self):
        with patch("sys.argv",["song_show","--lyrics",str(FIXTURES/"lyrics-plan.json")]),patch("builtins.print") as emit:
            self.assertEqual(show_main(),0)
        output="\n".join(str(call.args[0]) for call in emit.call_args_list)
        self.assertIn("Title: Numărăm în grădină",output); self.assertLess(output.index("Verse:"),output.index("Chorus:"))
        self.assertIn("Numărăm și suntem voinici!",output)


if __name__=="__main__": unittest.main()
