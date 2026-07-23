import tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from pydantic import ValidationError

from app.providers import (KlingFirstFrameContent,KlingImageToVideoMapper,KlingImageToVideoProvider,
    KlingImageToVideoRequest,KlingPromptContent)
from app.visual_references import (CanonicalReferenceUrlUnavailableError,LUCA_MAX_SCENE_REFERENCE,SceneVisualReference,
    PublishedVisualReference,VisualReferencePublicationRegistry)
from app.production import EpisodeProductionPlanner,GenerationRequestStore,SceneDurationPolicy,StoryboardVideoPlanner
from app.storyboard import DeterministicStoryboardGenerator
from tests.test_canonical_characters import bible,brief,profile


class KlingImageToVideoTests(unittest.TestCase):
    def _request(self):
        storyboard=DeterministicStoryboardGenerator().generate_storyboard(brief(),bible(),(profile("luca"),profile("max")))
        class Characters:
            def require_many(self,ids): return tuple(profile(value) for value in ids)
        class Series:
            def load(self,_id): return bible()
        return StoryboardVideoPlanner(SceneDurationPolicy(10),Characters(),Series()).build(storyboard,"video")[0]

    def _prepared(self):
        request=self._request()
        reference=SceneVisualReference(reference_id="video-shot-0001-contextual",character_ids=("luca","max"),
            local_path=LUCA_MAX_SCENE_REFERENCE.local_path,sha256="a"*64,content_type="image/png",width=1920,height=1080)
        return request.model_copy(update={"scene_visual_reference":reference})

    def test_exact_order_and_mapping(self):
        resolver=Mock(); resolver.resolve.return_value="https://assets.example/reference.png"
        dto=KlingImageToVideoMapper(resolver).map(self._prepared(),"external")
        self.assertEqual([item.type for item in dto.contents],["prompt","first_frame"])
        self.assertEqual(str(dto.contents[1].url),"https://assets.example/reference.png")
        self.assertEqual(dto.settings.duration,10); self.assertFalse(dto.settings.multi_shot)
        self.assertNotIn("local_path",str(dto.to_payload()))

    def test_unknown_fields_rejected(self):
        with self.assertRaises(ValidationError): KlingPromptContent(text="x",unknown=True)

    def test_composite_must_represent_complete_cast(self):
        request=self._prepared(); incomplete=request.model_copy(update={"scene_visual_reference":
            request.scene_visual_reference.model_copy(update={"character_ids":("luca",)})})
        resolver=Mock(); resolver.resolve.return_value="https://assets.example/reference.png"
        with self.assertRaisesRegex(ValueError,"complete scene cast"):
            KlingImageToVideoMapper(resolver).map(incomplete,"external")

    def test_storyboard_planner_selects_image_provider(self):
        storyboard=DeterministicStoryboardGenerator().generate_storyboard(brief(),bible(),(profile("luca"),profile("max")))
        with tempfile.TemporaryDirectory() as root:
            planner=EpisodeProductionPlanner(Mock(),GenerationRequestStore(Path(root)/"requests"),SceneDurationPolicy(15))
            result=planner.preflight(storyboard,"video",Path(root)/"scenes",Path(root)/"work",Path(root)/"master.mp4")
        self.assertEqual("kling_image_to_video",result.provider)
        self.assertTrue(all(value.video_request.duration_seconds==10 for value in result.video_requests))

    def test_unprepared_contextual_frame_rejected_before_http(self):
        with tempfile.TemporaryDirectory() as root:
            mapper=KlingImageToVideoMapper(VisualReferencePublicationRegistry(Path(root)/"map.json"))
            client=Mock(); provider=KlingImageToVideoProvider(client,mapper)
            with self.assertRaisesRegex(ValueError,"workflow is not configured"): provider.submit_generation(self._request())
            client.post_json.assert_not_called()

    def test_generic_identity_sheet_is_never_a_production_first_frame(self):
        request=self._request().model_copy(update={"scene_visual_reference":LUCA_MAX_SCENE_REFERENCE})
        with self.assertRaisesRegex(ValueError,"generic canonical identity sheet"):
            KlingImageToVideoMapper(Mock()).map(request,"external")

    def test_motion_prompt_is_deterministic_and_semantics_are_not_duplicated(self):
        request=self._prepared(); mapper=KlingImageToVideoMapper(Mock())
        first=mapper._prompt(request); second=mapper._prompt(request)
        self.assertEqual(first,second)
        action=request.video_request.character_actions[0].action
        self.assertEqual(1,first.count(action))
        self.assertEqual(1,first.count("Motion and interaction:"))
        self.assertIn("without rebuilding the location",first)

    def test_publication_reused_by_hash(self):
        with tempfile.TemporaryDirectory() as root:
            registry=VisualReferencePublicationRegistry(Path(root)/"map.json"); publisher=Mock()
            publisher.publish.return_value=PublishedVisualReference(sha256=LUCA_MAX_SCENE_REFERENCE.sha256,
                https_url="https://assets.example/reference.png",remote_asset_id="asset")
            first=registry.publish_once(LUCA_MAX_SCENE_REFERENCE,publisher)
            second=registry.publish_once(LUCA_MAX_SCENE_REFERENCE,publisher)
            self.assertEqual(first,second); publisher.publish.assert_called_once()

    def test_existing_https_publication_registration_is_idempotent_without_upload(self):
        with tempfile.TemporaryDirectory() as root:
            registry=VisualReferencePublicationRegistry(Path(root)/"map.json")
            url="https://raw.githubusercontent.com/example/repository/reference.png"
            first=registry.register_existing(LUCA_MAX_SCENE_REFERENCE,url)
            second=registry.register_existing(LUCA_MAX_SCENE_REFERENCE,"https://different.example/unused.png")
            self.assertEqual(first,second)
            self.assertEqual(url,registry.resolve(LUCA_MAX_SCENE_REFERENCE))

    def test_existing_publication_requires_https(self):
        with tempfile.TemporaryDirectory() as root:
            registry=VisualReferencePublicationRegistry(Path(root)/"map.json")
            with self.assertRaises(CanonicalReferenceUrlUnavailableError):
                registry.register_existing(LUCA_MAX_SCENE_REFERENCE,"http://example.test/reference.png")


if __name__=="__main__": unittest.main()
