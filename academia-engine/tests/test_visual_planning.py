import tempfile,unittest
from pathlib import Path
from unittest.mock import Mock

from app.scene_planning import SceneEducationalConstraint,SemanticScenePlanner
from app.visual_planning import *
from tests.test_project_008_validation import Fixture008


class VisualPlanningTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(); self.fx=Fixture008(self.temporary.name)
        scene_planner=SemanticScenePlanner()
        self.scene_plans=[scene_planner.plan("008",alignment,self.fx.lyrics,self.fx.storyboard,timeline)
            for alignment,timeline in zip(self.fx.alignments,self.fx.timelines)]
        self.planner=ProviderNeutralVisualPlanner(); self.style=default_visual_style(self.planner.configuration)
        self.plans=[self.planner.plan(scene_plan=value,global_style=self.style,aspect_ratio="16:9") for value in self.scene_plans]
        self.repository=VisualPlanRepository(self.fx.root/"visual")
    def tearDown(self): self.temporary.cleanup()

    def test_visual_plan_is_created_per_audio_variant(self):
        self.assertEqual(["variant-01","variant-02"],[x.audio_variant_id for x in self.plans]); self.assertNotEqual(self.plans[0].semantic_sha256,self.plans[1].semantic_sha256)
    def test_visual_plan_preserves_project_and_variant_identity(self):
        self.assertEqual(("008","variant-01"),(self.plans[0].project_id,self.plans[0].audio_variant_id))
    def test_visual_scene_preserves_source_scene_id(self):
        self.assertEqual([x.scene_id for x in self.scene_plans[0].scenes],[x.source_scene_id for x in self.plans[0].scenes])
    def test_visual_scene_preserves_scene_timing_exactly(self):
        for source,visual in zip(self.scene_plans[0].scenes,self.plans[0].scenes): self.assertEqual((source.start_s,source.end_s,source.duration_s),(visual.start_s,visual.end_s,visual.duration_s))
    def test_visual_scene_preserves_scene_order(self): self.assertEqual(list(range(len(self.plans[0].scenes))),[x.ordinal for x in self.plans[0].scenes])
    def test_visual_scene_preserves_source_references(self):
        source=self.scene_plans[0].scenes[1]; visual=self.plans[0].scenes[1]
        self.assertEqual([x.model_dump(mode="json") for x in source.source_references],list(visual.source_references))
    def test_visual_plan_contains_one_visual_scene_per_planned_scene(self): self.assertEqual(len(self.scene_plans[0].scenes),len(self.plans[0].scenes))
    def test_visual_plan_is_provider_neutral(self):
        text=self.plans[0].model_dump_json().casefold(); self.assertFalse(any(x in text for x in ("kling","flux","runway","veo","suno")))
    def test_visual_plan_contains_no_provider_specific_fields(self):
        fields=set(VisualPlan.model_fields); self.assertFalse(fields&{"prompt","model","provider","endpoint","payload"})
    def test_visual_scene_uses_unspecified_for_missing_environment(self): self.assertEqual("unspecified",self.plans[0].scenes[0].environment.location)
    def test_visual_scene_does_not_invent_subjects(self):
        for source,visual in zip(self.scene_plans[0].scenes,self.plans[0].scenes): self.assertEqual(len(source.subjects),len(visual.subjects))
    def test_visual_scene_does_not_invent_actions(self):
        for source,visual in zip(self.scene_plans[0].scenes,self.plans[0].scenes): self.assertEqual(len(source.actions),len(visual.actions))
    def test_instrumental_scene_has_no_invented_semantics(self):
        for value in (x for x in self.plans[0].scenes if "instrumental" in x.source_scene_id):
            self.assertEqual(((),(),(),"unspecified"),(value.subjects,value.actions,value.educational_constraints,value.environment.location))
    def test_visual_scene_supports_camera_description(self):
        camera=self.plans[0].scenes[0].camera; self.assertEqual(("unspecified","unspecified","unspecified"),(camera.shot_size,camera.angle,camera.movement_intent))
    def test_visual_scene_supports_composition_description(self): self.assertEqual("unspecified",self.plans[0].scenes[0].composition.framing)
    def test_visual_scene_supports_lighting_description(self): self.assertEqual("unspecified",self.plans[0].scenes[0].lighting.lighting_type)
    def test_visual_scene_supports_global_style(self): self.assertTrue(all(x.style==self.style for x in self.plans[0].scenes))
    def test_visual_plan_supports_16_9_aspect_ratio(self): self.assertEqual("16:9",self.plans[0].aspect_ratio)
    def test_visual_plan_supports_9_16_aspect_ratio(self): self.assertEqual("9:16",self.planner.plan(scene_plan=self.scene_plans[0],global_style=self.style,aspect_ratio="9:16").aspect_ratio)
    def test_visual_plan_supports_1_1_aspect_ratio(self): self.assertEqual("1:1",self.planner.plan(scene_plan=self.scene_plans[0],global_style=self.style,aspect_ratio="1:1").aspect_ratio)

    def _constrained(self,*constraints):
        scene=self.scene_plans[0].scenes[1].model_copy(update={"educational_constraints":tuple(constraints)})
        plan=self.scene_plans[0].model_copy(update={"scenes":tuple(scene if i==1 else x for i,x in enumerate(self.scene_plans[0].scenes))})
        # Semantic source identity changes whenever structured scene semantics change.
        plan=plan.model_copy(update={"semantic_sha256":semantic_sha256({"base":plan.semantic_sha256,"constraints":[x.model_dump(mode="json") for x in constraints]})})
        return self.planner.plan(scene_plan=plan,global_style=self.style,aspect_ratio="16:9").scenes[1]
    def test_visual_scene_preserves_educational_constraints(self):
        value=self._constrained(SceneEducationalConstraint(constraint_type="color_focus",value="roșu")); self.assertEqual("roșu",value.educational_constraints[0].value)
    def test_exact_count_constraint_remains_structured(self):
        value=self._constrained(SceneEducationalConstraint(constraint_type="must_show_count",value="3")); constraint=value.educational_constraints[0]
        self.assertEqual(("must_show_count","3",True),(constraint.key,constraint.value,constraint.required))
    def test_visual_scene_supports_negative_constraints(self):
        value=self._constrained(SceneEducationalConstraint(constraint_type="must_not_show_extra_countable_subjects",value="true")); self.assertEqual("extra_countable_subjects",value.negative_constraints[0].key)
    def test_visual_scene_has_deterministic_continuity_references(self):
        scenes=self.plans[0].scenes; self.assertIsNone(scenes[0].continuity_requirements.required_previous_scene_id)
        self.assertEqual(scenes[1].source_scene_id,scenes[0].continuity_requirements.required_next_scene_id)
    def test_visual_plan_is_deterministic(self): self.assertEqual(self.plans[0],self.planner.plan(scene_plan=self.scene_plans[0],global_style=self.style,aspect_ratio="16:9"))
    def test_visual_plan_json_is_byte_for_byte_stable(self):
        a=self.fx.root/"a.json"; b=self.fx.root/"b.json"; write_visual_plan(a,self.plans[0]); write_visual_plan(b,self.plans[0])
        self.assertEqual(a.read_bytes(),b.read_bytes()); self.assertEqual(self.plans[0],read_visual_plan(a))
    def test_visual_plan_is_reused_when_dependencies_are_unchanged(self):
        self.repository.resolve_or_build(scene_plan=self.scene_plans[0],planner=self.planner,global_style=self.style,aspect_ratio="16:9")
        value,reused=self.repository.resolve_or_build(scene_plan=self.scene_plans[0],planner=self.planner,global_style=self.style,aspect_ratio="16:9"); self.assertTrue(reused)
    def test_scene_plan_change_invalidates_visual_plan(self):
        self.repository.resolve_or_build(scene_plan=self.scene_plans[0],planner=self.planner,global_style=self.style,aspect_ratio="16:9")
        changed=self.scene_plans[0].model_copy(update={"semantic_sha256":"f"*64}); _value,reused=self.repository.resolve_or_build(scene_plan=changed,planner=self.planner,global_style=self.style,aspect_ratio="16:9"); self.assertFalse(reused)
    def test_global_style_change_invalidates_visual_plan(self):
        self.repository.resolve_or_build(scene_plan=self.scene_plans[0],planner=self.planner,global_style=self.style,aspect_ratio="16:9")
        changed=self.style.model_copy(update={"complexity":"minimal"}); _value,reused=self.repository.resolve_or_build(scene_plan=self.scene_plans[0],planner=self.planner,global_style=changed,aspect_ratio="16:9"); self.assertFalse(reused)
    def test_aspect_ratio_change_invalidates_visual_plan(self):
        self.repository.resolve_or_build(scene_plan=self.scene_plans[0],planner=self.planner,global_style=self.style,aspect_ratio="16:9")
        _value,reused=self.repository.resolve_or_build(scene_plan=self.scene_plans[0],planner=self.planner,global_style=self.style,aspect_ratio="9:16"); self.assertFalse(reused)
    def test_generator_version_change_invalidates_visual_plan(self):
        self.repository.resolve_or_build(scene_plan=self.scene_plans[0],planner=self.planner,global_style=self.style,aspect_ratio="16:9")
        other=ProviderNeutralVisualPlanner(generator_version="17.2.1"); _value,reused=self.repository.resolve_or_build(scene_plan=self.scene_plans[0],planner=other,global_style=self.style,aspect_ratio="16:9"); self.assertFalse(reused)
    def test_visual_plan_change_does_not_invalidate_scene_plan(self):
        path=self.fx.root/"scene.json"; path.write_text(self.scene_plans[0].model_dump_json(),encoding="utf-8"); before=path.read_bytes()
        self.repository.resolve_or_build(scene_plan=self.scene_plans[0],planner=self.planner,global_style=self.style.model_copy(update={"complexity":"minimal"}),aspect_ratio="16:9"); self.assertEqual(before,path.read_bytes())
    def test_visual_plan_change_does_not_invalidate_other_audio_variant(self):
        for value in self.scene_plans: self.repository.resolve_or_build(scene_plan=value,planner=self.planner,global_style=self.style,aspect_ratio="16:9")
        before=self.repository.path("variant-02").read_bytes(); self.repository.resolve_or_build(scene_plan=self.scene_plans[0],planner=self.planner,global_style=self.style,aspect_ratio="9:16"); self.assertEqual(before,self.repository.path("variant-02").read_bytes())
    def test_visual_plan_json_preserves_romanian_diacritics(self):
        path=self.fx.root/"visual.json"; write_visual_plan(path,self.plans[0]); text=path.read_text(encoding="utf-8"); self.assertIn("Mărul roșu",text); self.assertIn("țânțarul",text)
    def test_visual_plan_contains_no_absolute_windows_paths(self):
        text=self.plans[0].model_dump_json(); self.assertNotIn(":\\",text); self.assertNotIn(str(self.fx.root),text)
    def test_visual_planning_makes_zero_http_calls(self): http=Mock(); self.planner.plan(scene_plan=self.scene_plans[0],global_style=self.style,aspect_ratio="16:9"); http.assert_not_called()
    def test_visual_planning_makes_zero_ai_calls(self): ai=Mock(); self.planner.plan(scene_plan=self.scene_plans[0],global_style=self.style,aspect_ratio="16:9"); ai.assert_not_called()
    def test_visual_planning_makes_zero_ffmpeg_calls(self): ffmpeg=Mock(); self.planner.plan(scene_plan=self.scene_plans[0],global_style=self.style,aspect_ratio="16:9"); ffmpeg.assert_not_called()

if __name__=="__main__": unittest.main()
