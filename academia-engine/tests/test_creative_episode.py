import json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch

from pydantic import ValidationError

from app.cli.episode_generate_creative import main as episode_cli
from app.creative import (DeterministicEpisodeGenerator,EducationalCreativeBrief,EpisodeGenerationService,
    EpisodeGeneratorRegistry,EpisodeOutputConflictError,GeneratedEpisodeLanguageError,
    GeneratedEpisodeSceneOrderError,MAX_SCENE_COUNT,persist_episode_atomic)
from app.project import CreativeProjectGenerationService
from app.providers.openai_episode_provider import OpenAIEpisodeGenerator,OpenAIEpisodeResponseDTO


class CreativeEpisodeTests(unittest.TestCase):
    def setUp(self):
        self.examples=Path(__file__).resolve().parents[1]/"examples"/"smoke"
        self.brief=EducationalCreativeBrief.model_validate_json((self.examples/"creative-brief.json").read_text(encoding="utf-8"))

    def test_valid_brief_and_invalid_age_or_scene_bounds(self):
        self.assertEqual(self.brief.scene_count,3)
        for changes in ({"target_age_min":6,"target_age_max":5},{"scene_count":1},{"scene_count":MAX_SCENE_COUNT+1}):
            with self.subTest(changes=changes),self.assertRaises(ValidationError):
                EducationalCreativeBrief.model_validate({**self.brief.model_dump(),**changes})

    def test_deterministic_episode_is_stable_valid_and_locally_identified(self):
        service=EpisodeGenerationService(DeterministicEpisodeGenerator())
        first=service.generate(self.brief); second=service.generate(self.brief)
        self.assertEqual(first,second); self.assertEqual(first.id,self.brief.brief_id)
        self.assertEqual([scene.number for scene in first.scenes],[1,2,3])
        self.assertTrue(all(scene.character_ids==[f"{self.brief.brief_id}-guide"] for scene in first.scenes))

    def test_service_calls_once_overrides_provider_id_and_rejects_semantic_mismatch(self):
        valid=DeterministicEpisodeGenerator().generate_episode(self.brief)
        class FakeGenerator:
            def __init__(self,value): self.value=value; self.calls=[]
            def generate_episode(self,brief): self.calls.append(brief); return self.value
        generator=FakeGenerator(valid.model_copy(update={"id":"provider-controlled"}))
        episode=EpisodeGenerationService(generator).generate(self.brief)
        self.assertEqual(generator.calls,[self.brief]); self.assertEqual(episode.id,self.brief.brief_id)
        duplicate=valid.model_copy(update={"scenes":[valid.scenes[0],valid.scenes[0],valid.scenes[2]]})
        generator.value=duplicate
        with self.assertRaises(GeneratedEpisodeSceneOrderError): EpisodeGenerationService(generator).generate(self.brief)
        mismatch=valid.model_copy(update={"metadata":valid.metadata.model_copy(update={"language":"en"})})
        generator.value=mismatch
        with self.assertRaises(GeneratedEpisodeLanguageError): EpisodeGenerationService(generator).generate(self.brief)

    def test_openai_dto_mapping_uses_responses_parse_once_and_local_ids(self):
        dto=OpenAIEpisodeResponseDTO.model_validate({"title":"Numărăm în grădină","lyrics":"Un cântec original.",
            "main_character":{"name":"Lumi","role":"ghid","description":"O buburuză prietenoasă.","appearance":"Roșie cu puncte."},
            "scenes":[{"narration":f"Scena {i}","visual_description":f"Imagine {i}","duration_seconds":25,
                "location":{"name":"Grădină","description":"Grădină luminoasă.","time_of_day":"morning"},
                "camera":{"shot_type":"wide","angle":"eye_level","movement":"pan","description":"Mișcare lentă."}}
                for i in range(1,4)]})
        client=Mock(); client.responses.parse.return_value=Mock(output_parsed=dto)
        episode=OpenAIEpisodeGenerator(client=client,model="gpt-5.6").generate_episode(self.brief)
        client.responses.parse.assert_called_once(); self.assertEqual(episode.id,self.brief.brief_id)
        self.assertEqual([scene.number for scene in episode.scenes],[1,2,3])
        self.assertEqual(client.responses.parse.call_args.kwargs["text_format"],OpenAIEpisodeResponseDTO)

    def test_registry_is_lazy_and_deterministic_requires_no_configuration(self):
        with patch.dict("os.environ",{"OPENAI_API_KEY":""},clear=False):
            self.assertIsInstance(EpisodeGeneratorRegistry().resolve("deterministic"),DeterministicEpisodeGenerator)
        factory=Mock(return_value=DeterministicEpisodeGenerator()); registry=EpisodeGeneratorRegistry({"openai":factory})
        self.assertEqual(factory.call_count,0); registry.resolve("openai"); factory.assert_called_once()

    def test_atomic_writer_conflict_and_overwrite(self):
        episode=EpisodeGenerationService(DeterministicEpisodeGenerator()).generate(self.brief)
        with tempfile.TemporaryDirectory() as directory:
            destination=Path(directory)/"episode.json"; persist_episode_atomic(episode,destination)
            self.assertEqual(json.loads(destination.read_text(encoding="utf-8"))["id"],self.brief.brief_id)
            with self.assertRaises(EpisodeOutputConflictError): persist_episode_atomic(episode,destination)
            persist_episode_atomic(episode,destination,overwrite=True); self.assertFalse(destination.with_suffix(".json.part").exists())

    def test_external_no_confirm_constructs_no_provider_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/"episode.json"; argv=["episode_generate_creative","--brief",str(self.examples/"creative-brief.json"),
                "--generator","openai","--output",str(output)]
            with patch("sys.argv",argv),patch("app.cli.episode_generate_creative.load_application_environment"),patch(
                    "app.cli.episode_generate_creative.EpisodeGeneratorRegistry") as registry,patch("builtins.print") as emit:
                self.assertEqual(episode_cli(),2)
            registry.assert_not_called(); self.assertFalse(output.exists())
            text="\n".join(str(call.args[0]) for call in emit.call_args_list); self.assertNotIn("prompt",text.lower())

    def test_creative_project_delegates_episode_to_existing_project_service(self):
        episode_service=Mock(); episode_service.generate.return_value=DeterministicEpisodeGenerator().generate_episode(self.brief)
        project=Mock(); project.generate.return_value=Mock(); registry=Mock(); registry.exists.return_value=False
        CreativeProjectGenerationService(episode_service,project,registry).generate(self.brief,"project-id",Path("out"),Mock(),Mock())
        episode_service.generate.assert_called_once_with(self.brief); project.generate.assert_called_once()


if __name__=="__main__": unittest.main()
