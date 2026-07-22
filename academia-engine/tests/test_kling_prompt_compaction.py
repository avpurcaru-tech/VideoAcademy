import tempfile,unittest
from pathlib import Path

from app.config import (KlingGenerationSettings,KLING_PROMPT_MAX_CHARACTERS,
    KLING_PROMPT_RECOMMENDED_CHARACTERS)
from app.models import (Camera,CharacterAction,Transition,VideoCharacter,VideoEnvironment,
    VideoGenerationRequest,VideoRequest)
from app.production import StoryboardVideoPlanner
from app.providers import KlingPromptTooLongError,KlingTextToVideoMapper
from tests.test_series_bible import setup_registries
from app.creative import EducationalCreativeBrief
from app.storyboard import DeterministicStoryboardGenerator
from tests.test_series_bible import brief_payload,profiles


def request(description="garden",action="Find the red ball and point to every requested color.",appearances=None):
    appearances=appearances or (("luca","Luca","child","golden-blond hair, blue eyes, age 4-5, light-blue T-shirt, beige shorts, white sneakers."),
        ("max","Max","dog","six-month-old German Shepherd puppy, black-and-tan fur, brown eyes, red collar. Max never speaks."))
    characters=[VideoCharacter(id=i,name=n,role=r,appearance=a) for i,n,r,a in appearances]
    return VideoGenerationRequest(request_id="request-1",video_request=VideoRequest(scene_number=1,duration_seconds=15,
        environment=VideoEnvironment(location_name="park",location_description=description,time_of_day="day",
            lighting_description="warm",lighting_intensity="medium"),characters=characters,
        character_actions=[CharacterAction(character_id=value.id,action=action,emotion="focused") for value in characters],
        camera=Camera(shot_type="wide",angle="eye_level",movement="static",description="Stable eye-level camera."),
        transition=Transition(type="cut")))


class KlingPromptCompactionTests(unittest.TestCase):
    def test_documented_boundary_accepts_3072_and_rejects_3073(self):
        mapper=KlingTextToVideoMapper(KlingGenerationSettings())
        self.assertEqual("x"*3072,mapper.validate_prompt("x"*3072))
        with self.assertRaises(KlingPromptTooLongError): mapper.validate_prompt("x"*3073)

    def test_long_prompt_compacts_deterministically_below_preferred_limit(self):
        value=request(description="colorful preschool park "*35,action="Find red, yellow, green, and blue objects. "*18)
        mapper=KlingTextToVideoMapper(KlingGenerationSettings()); first,one=mapper.prompt_with_diagnostic(value); second,two=mapper.prompt_with_diagnostic(value)
        self.assertEqual((first,one),(second,two)); self.assertTrue(one.compaction_applied)
        self.assertLessEqual(one.after_characters,KLING_PROMPT_RECOMMENDED_CHARACTERS)
        for required in ("golden-blond","blue eyes","light-blue T-shirt","German Shepherd","black-and-tan","red collar","Max never speaks","Find red"):
            self.assertIn(required,first)
        self.assertEqual(1,first.count("Luca [luca]")); self.assertEqual(1,first.count("Max [max]"))

    def test_compaction_does_not_mutate_canonical_profiles(self):
        before=tuple(value.model_dump_json() for value in profiles())
        brief=EducationalCreativeBrief.model_validate(brief_payload())
        with tempfile.TemporaryDirectory() as temporary:
            series,characters=setup_registries(temporary)
            storyboard=DeterministicStoryboardGenerator().generate_storyboard(brief,series.load(brief.series_id),profiles())
            generated=StoryboardVideoPlanner(character_registry=characters,series_registry=series).build(storyboard,"production")
            for value in generated: KlingTextToVideoMapper(KlingGenerationSettings()).map(value,"external")
        self.assertEqual(before,tuple(value.model_dump_json() for value in profiles()))

    def test_uncompactable_prompt_fails_locally(self):
        huge=(("one","One","character","a"*1000),("two","Two","character","b"*1000))
        value=request(description="c"*1000,action="d"*1000,appearances=huge)
        prompt,diagnostic=KlingTextToVideoMapper.prompt_with_diagnostic(value)
        self.assertGreater(diagnostic.after_characters,KLING_PROMPT_MAX_CHARACTERS)
        with self.assertRaises(KlingPromptTooLongError): KlingTextToVideoMapper(KlingGenerationSettings()).map(value,"external")


if __name__=="__main__": unittest.main()
