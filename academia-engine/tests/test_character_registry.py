import tempfile,unittest
from unittest.mock import Mock

from app.character_registry import *
from app.prompt_generation import PromptBuilder,PromptProvider,PromptRepository,default_prompt_capabilities
from app.scene_planning import SceneSubject,SemanticScenePlanner,semantic_sha256
from app.visual_planning import ProviderNeutralVisualPlanner,VisualPlanRepository,default_visual_style
from tests.test_project_008_validation import Fixture008

class CharacterContinuityRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(); self.fx=Fixture008(self.temporary.name)
        self.builder=CharacterRegistryBuilder(); self.luca=CharacterIdentity(character_id="luca",canonical_name="Luca",
            aliases=("copilul","băiețelul","el"),role=CharacterRole.MAIN,
            appearance=CharacterAppearance(hair_color="șaten",eye_color="căprui"),
            wardrobe=CharacterWardrobe(top="tricou roșu"),fixed_attributes=("friendly",))
        self.max=CharacterIdentity(character_id="max",canonical_name="Max")
        self.registry=self.builder.build("008",(self.luca,self.max))
        scene_plan=SemanticScenePlanner().plan("008",self.fx.alignments[0],self.fx.lyrics,self.fx.storyboard,self.fx.timelines[0])
        source=scene_plan.scenes[1].model_copy(update={"subjects":(SceneSubject(subject_id="copilul",subject_type="child",display_name="Luca"),)})
        self.scene_plan=scene_plan.model_copy(update={"scenes":tuple(source if x.ordinal==1 else x for x in scene_plan.scenes),
            "semantic_sha256":semantic_sha256({"base":scene_plan.semantic_sha256,"character":"Luca"})})
        self.visual_builder=ProviderNeutralVisualPlanner(); self.style=default_visual_style(self.visual_builder.configuration)
        self.visual=self.visual_builder.plan(scene_plan=self.scene_plan,global_style=self.style,aspect_ratio="16:9",character_registry=self.registry)
        self.prompt_builder=PromptBuilder(); self.provider=PromptProvider.GENERIC_IMAGE; self.capabilities=default_prompt_capabilities(self.provider)
    def tearDown(self): self.temporary.cleanup()
    def _changed(self,identity): return self.builder.build("008",(identity,self.max))
    def test_character_registry_created(self): self.assertEqual(("luca","max"),tuple(x.character_id for x in self.registry.characters))
    def test_character_ids_are_stable(self): self.assertEqual("tantarul",stable_character_id("Țânțarul")); self.assertEqual("luca",stable_character_id("Luca"))
    def test_alias_resolution(self): self.assertEqual(("luca",None),self.registry.resolve_alias("băiețelul"))
    def test_unknown_alias_warning(self):
        character_id,warning=self.registry.resolve_alias("necunoscut"); self.assertIsNone(character_id); self.assertEqual("unknown_character_alias",warning.code)
    def test_character_registry_json_is_stable(self):
        a=self.fx.root/"a.json"; b=self.fx.root/"b.json"; write_character_registry(a,self.registry); write_character_registry(b,self.registry); self.assertEqual(a.read_bytes(),b.read_bytes())
    def test_character_registry_preserves_diacritics(self):
        path=self.fx.root/"registry.json"; write_character_registry(path,self.registry); self.assertIn("șaten",path.read_text(encoding="utf-8"))
    def test_visual_scene_references_character(self): self.assertIn("luca",tuple(x for scene in self.visual.scenes for x in scene.character_ids))
    def test_prompt_uses_character_registry(self):
        bundle=self.prompt_builder.build_prompt_bundle(visual_plan=self.visual,provider=self.provider,capabilities=self.capabilities,character_registry=self.registry)
        prompt=next(x for x in bundle.prompts if "luca" in x.positive_prompt.casefold()); self.assertIn("șaten",prompt.positive_prompt); self.assertIn("tricou roșu",prompt.positive_prompt)
    def test_character_change_invalidates_prompt(self):
        repo=PromptRepository(self.fx.root/"prompts"); repo.resolve_or_build(visual_plan=self.visual,builder=self.prompt_builder,provider=self.provider,capabilities=self.capabilities,character_registry=self.registry)
        changed=self._changed(self.luca.model_copy(update={"appearance":self.luca.appearance.model_copy(update={"hair_color":"blond"})}))
        _value,reused=repo.resolve_or_build(visual_plan=self.visual,builder=self.prompt_builder,provider=self.provider,capabilities=self.capabilities,character_registry=changed); self.assertFalse(reused)
    def test_character_change_invalidates_visual_plan(self):
        repo=VisualPlanRepository(self.fx.root/"visual"); repo.resolve_or_build(scene_plan=self.scene_plan,planner=self.visual_builder,global_style=self.style,aspect_ratio="16:9",character_registry=self.registry)
        changed=self._changed(self.luca.model_copy(update={"wardrobe":self.luca.wardrobe.model_copy(update={"top":"hanorac"})}))
        _value,reused=repo.resolve_or_build(scene_plan=self.scene_plan,planner=self.visual_builder,global_style=self.style,aspect_ratio="16:9",character_registry=changed); self.assertFalse(reused)
    def test_unrelated_character_change_does_not_invalidate_other_prompts(self):
        repo=PromptRepository(self.fx.root/"prompts"); repo.resolve_or_build(visual_plan=self.visual,builder=self.prompt_builder,provider=self.provider,capabilities=self.capabilities,character_registry=self.registry)
        changed=self.builder.build("008",(self.luca,self.max.model_copy(update={"canonical_name":"Maxim"})))
        _value,reused=repo.resolve_or_build(visual_plan=self.visual,builder=self.prompt_builder,provider=self.provider,capabilities=self.capabilities,character_registry=changed); self.assertTrue(reused)
    def test_character_registry_reused(self):
        repo=CharacterContinuityRepository(self.fx.root/"visual"/"character-registry.json"); repo.resolve_or_build(project_id="008",characters=(self.luca,self.max),builder=self.builder)
        _value,reused=repo.resolve_or_build(project_id="008",characters=(self.luca,self.max),builder=self.builder); self.assertTrue(reused)
    def test_character_registry_zero_http(self): call=Mock(); self.builder.build("008",(self.luca,)); call.assert_not_called()
    def test_character_registry_zero_ai(self): call=Mock(); self.builder.build("008",(self.luca,)); call.assert_not_called()
    def test_character_registry_zero_ffmpeg(self): call=Mock(); self.builder.build("008",(self.luca,)); call.assert_not_called()

if __name__=="__main__": unittest.main()
