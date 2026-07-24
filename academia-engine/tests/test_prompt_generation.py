import tempfile,unittest
from unittest.mock import Mock

from app.prompt_generation import *
from app.scene_planning import SemanticScenePlanner,semantic_sha256
from app.visual_planning import (ProviderNeutralVisualPlanner,ShotSize,VisualAction,VisualEnvironment,VisualNegativeConstraint,VisualSubject,
    VisualConstraint,default_visual_style,write_visual_plan)
from tests.test_project_008_validation import Fixture008

class PromptGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(); self.fx=Fixture008(self.temporary.name)
        sp=SemanticScenePlanner(); vp=ProviderNeutralVisualPlanner(); style=default_visual_style(vp.configuration)
        self.visual_plans=[]
        for alignment,timeline in zip(self.fx.alignments,self.fx.timelines):
            scene=sp.plan("008",alignment,self.fx.lyrics,self.fx.storyboard,timeline)
            self.visual_plans.append(vp.plan(scene_plan=scene,global_style=style,aspect_ratio="16:9"))
        self.builder=PromptBuilder(); self.provider=PromptProvider.GENERIC_IMAGE
        self.capabilities=default_prompt_capabilities(self.provider)
        self.bundles=[self.builder.build_prompt_bundle(visual_plan=x,provider=self.provider,capabilities=self.capabilities) for x in self.visual_plans]
        self.repository=PromptRepository(self.fx.root/"visual"/"prompts")
    def tearDown(self): self.temporary.cleanup()
    def _rich(self):
        scene=self.visual_plans[0].scenes[1].model_copy(update={"subjects":(VisualSubject(subject_type="apple",display_name="Mărul roșu"),),
            "actions":(VisualAction(action_type="rolls gently"),),"environment":VisualEnvironment(location="garden")})
        return self.builder.build_scene_prompt(scene=scene,variant_id="variant-01",provider=self.provider,capabilities=self.capabilities)
    def _count_plan(self):
        source=self.visual_plans[0].scenes[1]
        subject=VisualSubject(subject_type="duckling",display_name="ducklings")
        constraints=(VisualConstraint(constraint_type="count",key="must_show_count",value="3",required=True),
            VisualConstraint(constraint_type="count",key="must_not_show_extra_countable_subjects",value="true",required=True))
        negative=(VisualNegativeConstraint(constraint_type="count",key="extra_countable_subjects",value=False,reason="Preserve exact educational count"),)
        changed=source.model_copy(update={"subjects":(subject,),"educational_constraints":constraints,
            "positive_constraints":constraints,"negative_constraints":negative})
        plan=self.visual_plans[0].model_copy(update={"scenes":tuple(changed if x==source else x for x in self.visual_plans[0].scenes),
            "semantic_sha256":semantic_sha256({"base":self.visual_plans[0].semantic_sha256,"count":3})})
        return plan
    def test_prompt_bundle_created_per_variant(self): self.assertEqual(["variant-01","variant-02"],[x.variant_id for x in self.bundles])
    def test_one_prompt_per_visual_scene(self): self.assertEqual(len(self.visual_plans[0].scenes),len(self.bundles[0].prompts))
    def test_positive_prompt_contains_subjects(self): self.assertIn(self._rich().structured_parameters["subjects"][0]["display_name"],self._rich().positive_prompt)
    def test_positive_prompt_contains_actions(self): self.assertIn("rolls gently",self._rich().positive_prompt)
    def test_positive_prompt_contains_environment(self): self.assertIn("environment",self._rich().positive_prompt)
    def test_positive_prompt_contains_style(self): self.assertIn("style medium",self._rich().positive_prompt)
    def test_positive_prompt_contains_camera(self):
        scene=self.visual_plans[0].scenes[0].model_copy(update={"camera":self.visual_plans[0].scenes[0].camera.model_copy(update={"shot_size":ShotSize.WIDE})})
        self.assertIn("camera shot_size: wide",self.builder.build_scene_prompt(scene=scene,variant_id="variant-01",provider=self.provider,capabilities=self.capabilities).positive_prompt)
    def test_positive_prompt_contains_lighting(self):
        scene=self.visual_plans[0].scenes[0].model_copy(update={"lighting":self.visual_plans[0].scenes[0].lighting.model_copy(update={"brightness":"bright"})})
        self.assertIn("lighting brightness: bright",self.builder.build_scene_prompt(scene=scene,variant_id="variant-01",provider=self.provider,capabilities=self.capabilities).positive_prompt)
    def test_positive_prompt_contains_educational_constraints(self): self.assertIn("educational constraint",self.builder.build_prompt_bundle(visual_plan=self._count_plan(),provider=self.provider,capabilities=self.capabilities).prompts[1].positive_prompt)
    def test_exact_count_is_preserved(self):
        prompt=next(x for x in self.builder.build_prompt_bundle(visual_plan=self._count_plan(),provider=self.provider,capabilities=self.capabilities).prompts if x.structured_parameters["counts"])
        self.assertIn("exactly three ducklings",prompt.positive_prompt); self.assertEqual({"subject":"duckling","count":3,"exact":True},prompt.structured_parameters["counts"][0])
    def test_negative_prompt_uses_negative_constraints(self):
        prompt=next(x for x in self.builder.build_prompt_bundle(visual_plan=self._count_plan(),provider=self.provider,capabilities=self.capabilities).prompts if x.negative_prompt)
        self.assertIn("no additional countable subjects",prompt.negative_prompt)
    def test_structured_parameters_are_complete(self): self.assertEqual({"aspect_ratio","duration","camera","lighting","style","subjects","counts","educational_constraints"},set(self._rich().structured_parameters))
    def test_prompt_bundle_is_deterministic(self): self.assertEqual(self.bundles[0],self.builder.build_prompt_bundle(visual_plan=self.visual_plans[0],provider=self.provider,capabilities=self.capabilities))
    def test_prompt_json_is_stable(self):
        a=self.fx.root/"a.json"; b=self.fx.root/"b.json"; write_prompt_bundle(a,self.bundles[0]); write_prompt_bundle(b,self.bundles[0]); self.assertEqual(a.read_bytes(),b.read_bytes())
    def test_prompt_bundle_is_reused(self):
        self.repository.resolve_or_build(visual_plan=self.visual_plans[0],builder=self.builder,provider=self.provider,capabilities=self.capabilities)
        _value,reused=self.repository.resolve_or_build(visual_plan=self.visual_plans[0],builder=self.builder,provider=self.provider,capabilities=self.capabilities); self.assertTrue(reused)
    def test_visual_plan_change_invalidates_prompt_bundle(self):
        self.repository.resolve_or_build(visual_plan=self.visual_plans[0],builder=self.builder,provider=self.provider,capabilities=self.capabilities)
        changed=self.visual_plans[0].model_copy(update={"semantic_sha256":"f"*64}); _value,reused=self.repository.resolve_or_build(visual_plan=changed,builder=self.builder,provider=self.provider,capabilities=self.capabilities); self.assertFalse(reused)
    def test_prompt_bundle_does_not_invalidate_visual_plan(self):
        path=self.fx.root/"visual.json"; write_visual_plan(path,self.visual_plans[0]); before=path.read_bytes()
        self.repository.resolve_or_build(visual_plan=self.visual_plans[0],builder=self.builder,provider=self.provider,capabilities=self.capabilities); self.assertEqual(before,path.read_bytes())
    def test_provider_capability_warning(self):
        caps=PromptCapabilities(supports_negative_prompt=False,supports_duration=False,supports_aspect_ratio=False)
        bundle=self.builder.build_prompt_bundle(visual_plan=self._count_plan(),provider=self.provider,capabilities=caps)
        self.assertTrue({"duration_unsupported","aspect_ratio_unsupported","negative_prompt_unsupported"}<={x.code for x in bundle.warnings})
    def test_generic_provider_supported(self): self.assertEqual(self.provider,self.bundles[0].provider)
    def test_generic_video_provider_supported(self): self.assertEqual("generic_video",self.builder.build_prompt_bundle(visual_plan=self.visual_plans[0],provider="generic_video",capabilities=default_prompt_capabilities("generic_video")).provider)
    def test_kling_provider_serialization(self):
        bundle=self.builder.build_prompt_bundle(visual_plan=self.visual_plans[0],provider="kling",capabilities=default_prompt_capabilities("kling")); path=self.fx.root/"kling.json"; write_prompt_bundle(path,bundle)
        self.assertEqual(PromptProvider.KLING,read_prompt_bundle(path).provider)
    def test_prompt_generation_zero_http(self): call=Mock(); self.builder.build_prompt_bundle(visual_plan=self.visual_plans[0],provider=self.provider,capabilities=self.capabilities); call.assert_not_called()
    def test_prompt_generation_zero_ai(self): call=Mock(); self.builder.build_prompt_bundle(visual_plan=self.visual_plans[0],provider=self.provider,capabilities=self.capabilities); call.assert_not_called()
    def test_prompt_generation_zero_ffmpeg(self): call=Mock(); self.builder.build_prompt_bundle(visual_plan=self.visual_plans[0],provider=self.provider,capabilities=self.capabilities); call.assert_not_called()

if __name__=="__main__": unittest.main()
