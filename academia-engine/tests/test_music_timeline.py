import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from pydantic import ValidationError

from app.cli.timeline_generate import main as cli_main
from app.music_timeline import (InvalidMusicTimelineError, MusicTimeline, MusicTimelineGenerationService,
    MusicTimelineRepository, MusicTimelineSegment)
from app.providers.openai_music_timeline_provider import OpenAIMusicTimelineGenerator
from app.storyboard import DeterministicStoryboardGenerator, StoryboardLyricsAdapter

from tests.test_creative_storyboard import brief


def inputs():
    storyboard=DeterministicStoryboardGenerator().generate_storyboard(brief())
    return storyboard,StoryboardLyricsAdapter().adapt(storyboard)


def timeline():
    storyboard,_=inputs()
    return MusicTimeline(timeline_id="counting-story-music",storyboard_id="counting-story",
        music_duration_seconds=30,segments=tuple(MusicTimelineSegment(start_seconds=index*10,
            end_seconds=(index+1)*10,storyboard_section_id=section.section_id,estimated_confidence=.9)
            for index,section in enumerate(storyboard.sections)))


class FakeGenerator:
    def __init__(self,result): self.result=result; self.calls=[]
    def generate_timeline(self,storyboard,lyrics,duration):
        self.calls.append((storyboard,lyrics,duration)); return self.result


class MusicTimelineTests(unittest.TestCase):
    def test_contract_validates_intervals_confidence_order_and_duration(self):
        base=timeline().model_dump(mode="python")
        cases=[]
        gap=json.loads(json.dumps(base)); gap["segments"][1]["start_seconds"]=11; cases.append(gap)
        reversed_interval=json.loads(json.dumps(base)); reversed_interval["segments"][0]["end_seconds"]=0; cases.append(reversed_interval)
        confidence=json.loads(json.dumps(base)); confidence["segments"][0]["estimated_confidence"]=1.1; cases.append(confidence)
        ending=json.loads(json.dumps(base)); ending["music_duration_seconds"]=31; cases.append(ending)
        duplicate=json.loads(json.dumps(base)); duplicate["segments"][1]["storyboard_section_id"]=duplicate["segments"][0]["storyboard_section_id"]; cases.append(duplicate)
        for payload in cases:
            with self.subTest(payload=payload),self.assertRaises(ValidationError): MusicTimeline.model_validate(payload)

    def test_service_requires_storyboard_section_order_and_real_duration(self):
        storyboard,lyrics=inputs(); generated=timeline()
        generator=FakeGenerator(generated)
        result=MusicTimelineGenerationService(generator).generate(storyboard,lyrics,30)
        self.assertEqual(result,generated)
        self.assertEqual(generator.calls,[(storyboard,lyrics,30.0)])
        wrong=generated.model_copy(update={"segments":tuple(reversed(generated.segments))})
        generator.result=wrong
        with self.assertRaises(InvalidMusicTimelineError):
            MusicTimelineGenerationService(generator).generate(storyboard,lyrics,30)

    def test_atomic_persistence_round_trip_and_no_audio_write(self):
        value=timeline()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/"music-timelines"
            with patch("app.music_timeline.repository.os.replace",wraps=__import__("os").replace) as replace:
                destination=MusicTimelineRepository(root).save(value)
            self.assertEqual(destination,root/"counting-story"/"timeline.json")
            self.assertEqual(MusicTimelineRepository(root).load("counting-story"),value)
            replace.assert_called_once(); self.assertFalse(destination.with_suffix(".json.part").exists())
            self.assertEqual([path.suffix for path in root.rglob("*") if path.is_file()],[".json"])

    def test_contract_is_provider_neutral(self):
        schema=json.dumps(MusicTimeline.model_json_schema()).lower()
        for forbidden in ("openai","suno","kling","url","prompt","payload","ffmpeg"):
            self.assertNotIn(forbidden,schema)

    def test_openai_generator_uses_structured_output_with_mock_client(self):
        storyboard,lyrics=inputs(); expected=timeline(); client=Mock()
        client.responses.parse.return_value=type("Response",(),{"output_parsed":expected})()
        actual=OpenAIMusicTimelineGenerator(client=client).generate_timeline(storyboard,lyrics,30)
        self.assertEqual(actual,expected)
        self.assertEqual(client.responses.parse.call_args.kwargs["text_format"],MusicTimeline)

    def test_cli_without_confirm_never_resolves_provider_or_writes(self):
        storyboard,lyrics=inputs()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); storyboard_path=root/"storyboard.json"; lyrics_path=root/"lyrics.json"
            storyboard_path.write_text(storyboard.model_dump_json(),encoding="utf-8")
            lyrics_path.write_text(lyrics.model_dump_json(),encoding="utf-8")
            argv=["timeline_generate","--storyboard",str(storyboard_path),"--lyrics",str(lyrics_path),
                "--music-duration","30","--runtime-root",str(root/"timelines")]
            with patch("sys.argv",argv),patch("app.cli.timeline_generate.MusicTimelineGeneratorRegistry") as registry:
                self.assertEqual(cli_main(),2)
            registry.assert_not_called(); self.assertFalse((root/"timelines").exists())

    def test_confirmed_cli_with_mock_generator_persists_metadata_only(self):
        storyboard,lyrics=inputs()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); storyboard_path=root/"storyboard.json"; lyrics_path=root/"lyrics.json"
            storyboard_path.write_text(storyboard.model_dump_json(),encoding="utf-8")
            lyrics_path.write_text(lyrics.model_dump_json(),encoding="utf-8")
            argv=["timeline_generate","--storyboard",str(storyboard_path),"--lyrics",str(lyrics_path),
                "--music-duration","30","--runtime-root",str(root/"timelines"),"--confirm"]
            generator=FakeGenerator(timeline())
            with patch("sys.argv",argv),patch("app.cli.timeline_generate.MusicTimelineGeneratorRegistry") as registry:
                registry.return_value.resolve.return_value=generator; self.assertEqual(cli_main(),0)
            self.assertTrue((root/"timelines"/"counting-story"/"timeline.json").is_file())


if __name__=="__main__": unittest.main()
