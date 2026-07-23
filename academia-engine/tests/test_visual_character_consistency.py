import tempfile
import unittest
from pathlib import Path

from app.characters import CanonicalCharacterProfile, CharacterRegistry
from app.production import (RecurringCharacterReferenceInvalidError,
    RecurringCharacterReferenceMissingError, VisualConsistencyRetryPolicy)
from app.providers import KlingCharacterReferenceUnsupportedError, KlingProvider
from app.storyboard import DeterministicStoryboardGenerator
from tests.test_canonical_characters import CHARACTERS, bible, brief, profile
from app.series import SeriesRegistry
from app.production import StoryboardVideoPlanner


class VisualCharacterConsistencyTests(unittest.TestCase):
    def test_missing_recurring_reference_is_rejected_locally(self):
        storyboard=DeterministicStoryboardGenerator().generate_storyboard(brief(),bible(),(profile("luca"),profile("max")))
        no_reference=profile("luca").model_copy(update={"visual_reference":None})
        registry=FakeCharacters((no_reference,profile("max")))
        with self.assertRaises(RecurringCharacterReferenceMissingError):
            StoryboardVideoPlanner(character_registry=registry,series_registry=FakeSeries()).build(storyboard,"video")

    def test_text_endpoint_rejects_references_before_http(self):
        storyboard=DeterministicStoryboardGenerator().generate_storyboard(brief(),bible(),(profile("luca"),profile("max")))
        request=StoryboardVideoPlanner(character_registry=FakeCharacters((profile("luca"),profile("max"))),
            series_registry=FakeSeries()).build(storyboard,"video")[0]
        client=FailIfCalledClient()
        with self.assertRaises(KlingCharacterReferenceUnsupportedError):
            KlingProvider(client=client).submit_generation(request)
        self.assertEqual(0,client.calls)

    def test_retry_policy_retries_only_once(self):
        policy=VisualConsistencyRetryPolicy(max_identity_retries=1)
        self.assertTrue(policy.can_retry(0)); self.assertFalse(policy.can_retry(1))


class FakeCharacters:
    def __init__(self,values): self.values={value.character_id:value for value in values}
    def require_many(self,ids): return tuple(self.values[value] for value in ids)

class FakeSeries:
    def load(self,_series_id): return bible()

class FailIfCalledClient:
    calls=0
    def post_json(self,*_args,**_kwargs): self.calls+=1; raise AssertionError("HTTP must not be called")


if __name__=="__main__": unittest.main()
