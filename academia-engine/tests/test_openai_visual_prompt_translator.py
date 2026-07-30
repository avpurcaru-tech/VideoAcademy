import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from app.providers.openai_visual_prompt_translator import OpenAIVisualPromptTranslator,TranslatedVisualScene,TranslatedVisualScenes


class OpenAIVisualPromptTranslatorTests(unittest.TestCase):
    def test_translates_all_scenes_in_one_structured_call(self):
        client=SimpleNamespace(responses=Mock()); client.responses.parse.return_value=SimpleNamespace(output_parsed=TranslatedVisualScenes(scenes=(
            TranslatedVisualScene(scene_id="scene-1",english_visual_direction="A small flower opens in a green spring meadow."),
            TranslatedVisualScene(scene_id="scene-2",english_visual_direction="Birds return as gentle rain falls."))))
        result=OpenAIVisualPromptTranslator(None,client=client).translate({"scene-1":["Floare mică se deschide"],"scene-2":["Păsările se întorc"]})
        self.assertEqual(2,len(result)); client.responses.parse.assert_called_once()
        self.assertIs(client.responses.parse.call_args.kwargs["text_format"],TranslatedVisualScenes)

    def test_missing_scene_is_rejected(self):
        client=SimpleNamespace(responses=Mock()); client.responses.parse.return_value=SimpleNamespace(output_parsed=TranslatedVisualScenes(scenes=(
            TranslatedVisualScene(scene_id="scene-1",english_visual_direction="A flower opens."),)))
        with self.assertRaises(ValueError): OpenAIVisualPromptTranslator(None,client=client).translate({"scene-1":["Floare"],"scene-2":["Păsări"]})

    def test_empty_input_makes_no_call(self):
        client=SimpleNamespace(responses=Mock()); self.assertEqual({},OpenAIVisualPromptTranslator(None,client=client).translate({}))
        client.responses.parse.assert_not_called()

    def test_provider_failure_is_safe_and_actionable(self):
        client=SimpleNamespace(responses=Mock()); client.responses.parse.side_effect=RuntimeError("secret provider body")
        with self.assertRaisesRegex(ValueError,"Check the OpenAI configuration") as caught:
            OpenAIVisualPromptTranslator(None,client=client).translate({"scene-1":["Floare"]})
        self.assertNotIn("secret provider body",str(caught.exception))


if __name__=="__main__": unittest.main()
