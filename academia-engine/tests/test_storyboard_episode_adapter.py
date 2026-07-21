import unittest
from unittest.mock import Mock, patch

from app.creative import EpisodeGenerationService
from app.models import Episode
from app.storyboard import (DeterministicStoryboardGenerator, EpisodeService,
    StoryboardEpisodeAdapter)

from tests.test_creative_storyboard import brief


class StoryboardEpisodeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.storyboard = DeterministicStoryboardGenerator().generate_storyboard(brief())

    def test_storyboard_to_episode_is_equal_across_derivations(self):
        adapter = StoryboardEpisodeAdapter()
        first = adapter.adapt(self.storyboard)
        second = adapter.adapt(self.storyboard)
        self.assertEqual(first, second)
        self.assertEqual(first.id, self.storyboard.storyboard_id)
        self.assertEqual([scene.number for scene in first.scenes], [1, 2, 3])
        self.assertEqual([scene.narration for scene in first.scenes],
                         [section.lyrics for section in self.storyboard.sections])

    def test_character_ids_are_stable_and_shared_by_name(self):
        episode = StoryboardEpisodeAdapter().adapt(self.storyboard)
        self.assertEqual(len(episode.characters), 1)
        expected = episode.characters[0].id
        self.assertTrue(expected.startswith("counting-story-character-"))
        self.assertTrue(all(scene.character_ids == [expected] for scene in episode.scenes))

    def test_episode_service_accepts_existing_episode_unchanged(self):
        episode = StoryboardEpisodeAdapter().adapt(self.storyboard)
        self.assertEqual(EpisodeService().resolve(episode), episode)

    def test_episode_generation_service_dispatches_without_generator_call(self):
        generator = Mock()
        generator.generate_episode = Mock(side_effect=AssertionError("provider call forbidden"))
        service = EpisodeGenerationService(generator)
        with patch("app.providers.openai_episode_provider.OpenAIEpisodeGenerator") as provider:
            episode = service.generate(self.storyboard)
        self.assertEqual(episode, StoryboardEpisodeAdapter().adapt(self.storyboard))
        generator.generate_episode.assert_not_called()
        provider.assert_not_called()

    def test_existing_episode_pipeline_dispatch_remains_unchanged(self):
        episode = StoryboardEpisodeAdapter().adapt(self.storyboard)
        generator = Mock(); generator.generate_episode = Mock(side_effect=AssertionError("must not run"))
        result = EpisodeGenerationService(generator).generate(episode)
        self.assertIsInstance(result, Episode)
        self.assertEqual(result, episode)
        generator.generate_episode.assert_not_called()


if __name__ == "__main__": unittest.main()
