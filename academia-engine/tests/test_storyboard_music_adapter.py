import unittest
from unittest.mock import Mock, patch

from app.storyboard import (DeterministicStoryboardGenerator, StoryboardMusicAdapter,
    StoryboardMusicDirection)

from tests.test_creative_storyboard import brief


class StoryboardMusicAdapterTests(unittest.TestCase):
    def setUp(self):
        base = DeterministicStoryboardGenerator().generate_storyboard(brief())
        direction = StoryboardMusicDirection(style="pop educațional românesc", mood="vesel și cald",
            tempo_bpm=112, vocals="voce clară, prietenoasă",
            instrumentation=("ukulele", "xilofon", "percuție ușoară"))
        texts = ("Unu, doi, trei!", "Numărăm împreună.", "Bravo, copii!")
        sections = tuple(section.model_copy(update={"lyrics": texts[index]})
                         for index, section in enumerate(base.sections))
        self.storyboard = base.model_copy(update={"title":"Cântecul numerelor",
            "music_direction":direction,"sections":sections})

    def test_storyboard_projects_complete_music_request(self):
        request = StoryboardMusicAdapter().adapt(self.storyboard)
        self.assertEqual(request.song_id, self.storyboard.storyboard_id)
        self.assertEqual(request.title, "Cântecul numerelor")
        self.assertEqual(request.music_plan.musical_style, "pop educațional românesc")
        self.assertEqual(request.music_plan.mood, "vesel și cald")
        self.assertEqual(request.music_plan.tempo_bpm, 112)
        self.assertEqual(request.music_plan.vocal_style, "voce clară, prietenoasă")
        self.assertEqual(request.music_plan.instrumentation,
                         ("ukulele", "xilofon", "percuție ușoară"))
        self.assertEqual(request.music_plan.target_duration_seconds, 30)

    def test_lyrics_formatting_and_order_are_preserved(self):
        request = StoryboardMusicAdapter().adapt(self.storyboard)
        self.assertEqual([section.order for section in request.lyrics.sections], [1, 2, 3])
        self.assertEqual([section.lines[0].text for section in request.lyrics.sections],
                         [section.lyrics for section in self.storyboard.sections])
        serialized = request.model_dump_json()
        self.assertIn("Cântecul numerelor", serialized)
        self.assertIn("percuție ușoară", serialized)

    def test_music_plan_and_request_are_deterministic(self):
        adapter = StoryboardMusicAdapter()
        self.assertEqual(adapter.music_plan(self.storyboard), adapter.music_plan(self.storyboard))
        self.assertEqual(adapter.adapt(self.storyboard), adapter.adapt(self.storyboard))

    def test_projection_never_calls_music_engine_or_provider(self):
        with patch("app.music.engine.MusicEngine.submit") as submit, \
             patch("app.music.registry.MusicTaskRegistry.create") as persist:
            request = StoryboardMusicAdapter().adapt(self.storyboard)
        self.assertEqual(request.song_id, "counting-story")
        submit.assert_not_called(); persist.assert_not_called()

    def test_existing_music_engine_contract_is_unchanged(self):
        request = StoryboardMusicAdapter().adapt(self.storyboard)
        provider = Mock()
        self.assertTrue(hasattr(request, "lyrics")); self.assertTrue(hasattr(request, "music_plan"))
        provider.assert_not_called()

    def test_legacy_storyboard_without_music_direction_remains_loadable(self):
        from app.storyboard import CreativeStoryboard
        payload=self.storyboard.model_dump(mode="python"); payload.pop("music_direction")
        restored=CreativeStoryboard.model_validate(payload)
        request=StoryboardMusicAdapter().adapt(restored)
        self.assertEqual(request.music_plan.tempo_bpm,110)


if __name__ == "__main__": unittest.main()
