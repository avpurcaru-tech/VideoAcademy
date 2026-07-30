import tempfile,unittest
from pathlib import Path
from unittest.mock import Mock

from app.lyrics_alignment import *
from app.providers.suno_timestamped_lyrics import SunoTimestampedLyricsAdapter
from app.song import LyricsLine,LyricsPlan,LyricsSection


def lyrics():
    return LyricsPlan(song_id="colors",title="Culori",language="ro",sections=(
        LyricsSection(section_id="verse-1",kind="verse",order=0,lines=(
            LyricsLine(line_id="v1",text="Mingea roșie sare"),LyricsLine(line_id="v2",text="Floarea galbenă răsare"))),
        LyricsSection(section_id="chorus-1",kind="chorus",order=1,lines=(LyricsLine(line_id="c1",text="Roșu, galben, verde, albastru!"),)),
        LyricsSection(section_id="chorus-2",kind="chorus",order=2,lines=(LyricsLine(line_id="c2",text="Roșu, galben, verde, albastru!"),))))

def words(offset=0):
    values="Mingea roșie sare Floarea galbenă răsare Roșu galben verde albastru Roșu galben verde albastru".split()
    return tuple(ProviderAlignedWord(text=value,start_seconds=float(offset+i),end_seconds=float(offset+i)+.5) for i,value in enumerate(values))

def build(provider_words=None,sha="a"*64,duration=30):
    return LyricsAlignmentNormalizer().build(variant_id="variant-01",audio_artifact_id="audio-1",audio_sha256=sha,
        provider_task_id="task-1",provider_audio_id="audio-1",audio_duration_seconds=duration,language="ro",
        source="suno_timestamped_lyrics",provider_words=provider_words or words(),lyrics=lyrics())


class LyricsAlignmentTests(unittest.TestCase):
    def test_romanian_diacritics_punctuation_and_repeated_refrain_map_by_sequence(self):
        value=build(); self.assertEqual("roșu galben verde albastru",normalize_lexical("Roșu, galben, verde, albastru!"))
        self.assertEqual({"v1","v2","c1","c2"},{line.source_lyrics_line_id for line in value.lines})
        self.assertEqual("valid",value.status); self.assertEqual(4,len(value.lines))

    def test_elongated_sung_vowel_is_matched_without_rewriting_display_text(self):
        changed=list(words()); changed[1]=ProviderAlignedWord(text="roșiiie",start_seconds=1.0,end_seconds=1.6)
        value=build(tuple(changed)); mapped=next(word for word in value.words if word.text=="roșiiie")
        self.assertEqual("v1",mapped.source_line_id); self.assertEqual("roșiiie",mapped.text)

    def test_two_variants_keep_different_timestamps(self):
        first=build(words(0)); second=LyricsAlignmentNormalizer().build(variant_id="variant-02",audio_artifact_id="audio-2",
            audio_sha256="b"*64,provider_task_id="task-1",provider_audio_id="audio-2",audio_duration_seconds=40,
            language="ro",source="suno_timestamped_lyrics",provider_words=words(5),lyrics=lyrics())
        self.assertNotEqual(first.words[0].start_seconds,second.words[0].start_seconds)

    def test_timestamp_beyond_audio_duration_is_invalid(self):
        with self.assertRaises(LyricsAlignmentInvalid): build(words(),duration=10)

    def test_small_allowed_provider_overrun_is_clamped_to_audio_duration(self):
        provider=list(words()); duration=provider[-1].end_seconds-.2
        value=build(tuple(provider),duration=duration)
        self.assertEqual(duration,value.words[-1].end_seconds); self.assertLessEqual(max(line.end_seconds for line in value.lines),duration)

    def test_word_entirely_inside_allowed_trailing_overrun_is_ignored(self):
        provider=list(words()); duration=provider[-1].start_seconds-.1
        provider[-1]=provider[-1].model_copy(update={"start_seconds":duration+.1,"end_seconds":duration+.3})
        value=build(tuple(provider),duration=duration)
        self.assertTrue(value.words); self.assertTrue(all(word.end_seconds<=duration for word in value.words))
        self.assertEqual(len(provider)-1,len(value.words))

    def test_zero_duration_provider_word_is_ignored(self):
        provider=list(words()); provider[-1]=provider[-1].model_copy(update={"end_seconds":provider[-1].start_seconds})
        value=build(tuple(provider))
        self.assertEqual(len(provider)-1,len(value.words))

    def test_instrumental_is_explicit(self):
        value=LyricsAlignmentNormalizer().build(variant_id="variant-01",audio_artifact_id="audio",audio_sha256="a"*64,
            provider_task_id="task",provider_audio_id="audio",audio_duration_seconds=20,language="ro",source="suno",
            provider_words=(),lyrics=lyrics(),instrumental=True)
        self.assertEqual("instrumental",value.status); self.assertFalse(value.words)

    def test_resume_reuses_hash_and_changed_mp3_hash_invalidates(self):
        with tempfile.TemporaryDirectory() as root:
            store=LyricsAlignmentStore(root); value=build(); store.save(value)
            self.assertEqual(value,store.load_valid("variant-01","a"*64)); self.assertIsNone(store.load_valid("variant-01","b"*64))

    def test_suno_adapter_uses_verified_endpoint_and_exact_fields(self):
        transport=Mock(); transport.request_json.return_value={"code":200,"msg":"success","data":{
            "alignedWords":[{"word":"roșie","success":True,"startS":1.2,"endS":1.6,"palign":0}],"hootCer":.1}}
        result=SunoTimestampedLyricsAdapter(transport).retrieve("task-1","audio-1")
        transport.request_json.assert_called_once_with("POST","/api/v1/generate/get-timestamped-lyrics",{"taskId":"task-1","audioId":"audio-1"})
        self.assertEqual("roșie",result.words[0].text)


if __name__=="__main__": unittest.main()
