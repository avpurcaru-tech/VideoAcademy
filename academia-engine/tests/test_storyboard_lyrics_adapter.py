import unittest
from unittest.mock import Mock, patch

from app.song import LyricsGenerationService, LyricsPlan, resolve_lyrics
from app.storyboard import DeterministicStoryboardGenerator, StoryboardLyricsAdapter

from tests.test_creative_storyboard import brief


class StoryboardLyricsAdapterTests(unittest.TestCase):
    def setUp(self):
        base = DeterministicStoryboardGenerator().generate_storyboard(brief())
        texts = (
            "Unu, doi, trei — numărăm împreună!\nȘi păstrăm diacriticele: ăâîșț.",
            "Numără cu mine:\nunu, doi, trei!",
            "Bravo, copii!\nAm învățat împreună.",
        )
        sections = tuple(section.model_copy(update={"lyrics": texts[index]})
                         for index, section in enumerate(base.sections))
        self.storyboard = base.model_copy(update={"sections": sections})

    def test_projection_is_exact_and_song_id_is_unchanged(self):
        lyrics = StoryboardLyricsAdapter().adapt(self.storyboard)
        self.assertEqual(lyrics.song_id, self.storyboard.storyboard_id)
        self.assertEqual(lyrics.title, self.storyboard.title)
        self.assertEqual(lyrics.language, "ro")
        self.assertEqual([section.section_id for section in lyrics.sections],
                         [section.section_id for section in self.storyboard.sections])
        self.assertEqual([section.order for section in lyrics.sections], [1, 2, 3])
        self.assertEqual([section.lines[0].text for section in lyrics.sections],
                         [section.lyrics for section in self.storyboard.sections])

    def test_unicode_romanian_and_multiline_formatting_round_trip(self):
        lyrics = StoryboardLyricsAdapter().adapt(self.storyboard)
        serialized = lyrics.to_json()
        restored = LyricsPlan.from_json(serialized)
        self.assertEqual(restored, lyrics)
        self.assertIn("ăâîșț", restored.sections[0].lines[0].text)
        self.assertEqual(restored.sections[0].lines[0].text.count("\n"), 1)
        self.assertEqual(resolve_lyrics(restored).structural_order,
                         tuple(section.section_id for section in self.storyboard.sections))

    def test_projection_is_deterministic(self):
        adapter = StoryboardLyricsAdapter()
        self.assertEqual(adapter.adapt(self.storyboard), adapter.adapt(self.storyboard))

    def test_generation_service_dispatches_before_generator_or_provider(self):
        generator = Mock(); generator.generate_lyrics = Mock(side_effect=AssertionError("provider call forbidden"))
        service = LyricsGenerationService(generator)
        with patch("app.providers.openai_lyrics_provider.OpenAILyricsGenerator") as provider:
            lyrics = service.generate(self.storyboard)
        self.assertEqual(lyrics, StoryboardLyricsAdapter().adapt(self.storyboard))
        generator.generate_lyrics.assert_not_called()
        provider.assert_not_called()

    def test_existing_brief_pipeline_remains_unchanged(self):
        expected = StoryboardLyricsAdapter().adapt(self.storyboard)
        generator = Mock(); generator.generate_lyrics = Mock(return_value=expected)
        from app.song import EducationalSongBrief
        song_brief = EducationalSongBrief(song_id=expected.song_id, topic="numere",
            learning_objectives=("numără",), language="ro", target_age_min=3,
            target_age_max=5, target_duration_seconds=30, tone="vesel", repetition_level="mare")
        self.assertEqual(LyricsGenerationService(generator).generate(song_brief), expected)
        generator.generate_lyrics.assert_called_once_with(song_brief)


if __name__ == "__main__": unittest.main()
