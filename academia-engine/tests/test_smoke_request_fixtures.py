import json
import unittest
from pathlib import Path

from app.cli.episode_smoke_test import _load_generation_request
from app.models import VideoGenerationRequest


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = (
    ROOT / "examples" / "smoke" / "scene-0001.request.json",
    ROOT / "examples" / "smoke" / "scene-0002.request.json",
)


class SmokeRequestFixtureTests(unittest.TestCase):
    def test_fixtures_deserialize_through_episode_smoke_path(self) -> None:
        requests = tuple(_load_generation_request(path) for path in FIXTURES)
        self.assertTrue(all(isinstance(request, VideoGenerationRequest) for request in requests))
        self.assertEqual([request.video_request.scene_number for request in requests], [1, 2])
        self.assertEqual([request.video_request.duration_seconds for request in requests], [15, 15])
        self.assertEqual([request.video_request.characters[0].id for request in requests], ["lila-ladybug", "lila-ladybug"])
        self.assertNotEqual(requests[0].video_request.environment.location_name, requests[1].video_request.environment.location_name)

    def test_fixtures_contain_only_approved_contract_fields_and_no_provider_secrets(self) -> None:
        allowed_top_level = set(VideoGenerationRequest.model_fields)
        for path in FIXTURES:
            with self.subTest(path=path.name):
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(set(payload), allowed_top_level)
                serialized = json.dumps(payload).lower()
                for forbidden in (
                    "authorization", "api_key", "api-key", "credential", "signed_url",
                    "callback_url", "external_task_id", "provider_metadata", "kling", "http://", "https://",
                ):
                    self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
