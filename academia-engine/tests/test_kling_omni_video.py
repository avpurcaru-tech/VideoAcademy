import unittest
from types import SimpleNamespace

from app.providers.kling_omni_video import KlingOmniUiPromptMapper


class KlingOmniVideoTests(unittest.TestCase):
    def test_character_image_is_reference_not_first_frame(self):
        settings=SimpleNamespace(duration=15,multi_shot=True)
        mapper=KlingOmniUiPromptMapper("Luca runs through spring grass",(("luca","https://example.test/luca.png"),),settings)
        payload=mapper.map(SimpleNamespace(),"external-1").to_payload()
        self.assertEqual("kling-v3-omni",payload["model_name"]); self.assertEqual([{"image_url":"https://example.test/luca.png"}],payload["image_list"])
        self.assertIn("<<<image_1>>>",payload["prompt"]); self.assertIn("do not use it as the first frame",payload["prompt"])
        self.assertNotIn("type",payload["image_list"][0])


if __name__=="__main__": unittest.main()
