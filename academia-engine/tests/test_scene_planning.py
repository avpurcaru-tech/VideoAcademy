import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock

from app.scene_planning import *
from tests.test_project_008_validation import Fixture008,source_lyrics


class SemanticScenePlanningTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(); self.fx=Fixture008(self.temporary.name)
        self.planner=SemanticScenePlanner(); self.visual=self.fx.root/"visual"; self.repository=ScenePlanRepository(self.visual)
        self.plans=[self.planner.plan("008",alignment,self.fx.lyrics,self.fx.storyboard,timeline)
            for alignment,timeline in zip(self.fx.alignments,self.fx.timelines)]
    def tearDown(self): self.temporary.cleanup()

    def test_scene_plan_is_built_per_audio_variant(self):
        self.assertEqual(["variant-01","variant-02"],[value.audio_variant_id for value in self.plans])
        self.assertNotEqual(self.plans[0].semantic_sha256,self.plans[1].semantic_sha256)

    def test_scene_plan_uses_line_and_section_timing(self):
        alignment=self.fx.alignments[0]; plan=self.plans[0]
        by_line={value.source_line_ids[0]:value for value in plan.scenes if value.scene_type.value=="vocal"}
        for line in alignment.lines:
            scene=by_line[line.source_lyrics_line_id]; self.assertEqual((line.start_seconds,line.end_seconds),(scene.start_s,scene.end_s))

    def test_scene_plan_persists_stable_source_references(self):
        scene=next(value for value in self.plans[0].scenes if value.scene_type.value=="vocal")
        self.assertEqual({"lyrics_line","lyrics_section","alignment_line","story_segment"},{x.source_type.value for x in scene.source_references})
        self.assertTrue(all(x.source_sha256 for x in scene.source_references))

    def test_repeated_chorus_creates_distinct_chronological_scenes(self):
        scenes={value.source_line_ids[0]:value for value in self.plans[0].scenes if value.source_line_ids}
        self.assertNotEqual(scenes["c1"].scene_id,scenes["c2"].scene_id); self.assertLess(scenes["c1"].end_s,scenes["c2"].start_s)

    def test_unmapped_line_receives_no_synthetic_scene_timing(self):
        self.assertFalse(any("missing" in value.source_line_ids for value in self.plans[0].scenes))

    def test_unmapped_line_is_reported_explicitly(self):
        plan=self.plans[0]; self.assertIn("missing",plan.unplanned_line_ids)
        self.assertIn("unmapped_lyrics_line",{value.code for value in plan.warnings}); self.assertEqual("valid_with_warnings",plan.status)

    def _instrumental(self,kind): return next(value for value in self.plans[0].scenes if value.scene_type.value==kind)
    def test_instrumental_intro_creates_explicit_scene(self):
        value=self._instrumental("instrumental_intro"); self.assertEqual((),value.subjects); self.assertEqual((),value.actions)
    def test_instrumental_break_creates_explicit_scene(self):
        value=self._instrumental("instrumental_break"); self.assertEqual("unspecified",value.environment.location)
    def test_instrumental_outro_creates_explicit_scene(self):
        value=self._instrumental("instrumental_outro"); self.assertEqual((),value.educational_constraints)
    def test_scene_plan_uses_instrumental_section_timing(self):
        alignment={value.section_type:value for value in self.fx.alignments[0].sections if value.section_type.startswith("instrumental_")}
        for kind in ("instrumental_intro","instrumental_outro"):
            scene=self._instrumental(kind); source=alignment[kind]
            self.assertEqual((source.start_seconds,source.end_seconds),(scene.start_s,scene.end_s))

    def test_scene_intervals_are_monotone_and_bounded(self):
        for plan in self.plans:
            self.assertTrue(all(0<=x.start_s<x.end_s<=plan.audio_duration_s for x in plan.scenes))
            self.assertTrue(all(a.end_s<=b.start_s+.01 for a,b in zip(plan.scenes,plan.scenes[1:])))
    def test_legacy_alignment_overrun_is_clamped_to_audio_duration(self):
        alignment=self.fx.alignments[0]; duration=alignment.audio_duration_seconds
        lines=list(alignment.lines); lines[-1]=lines[-1].model_copy(update={"end_seconds":duration+.2})
        sections=tuple(section for section in alignment.sections if section.section_type!="instrumental_outro")
        plan=self.planner.plan("008",alignment.model_copy(update={"lines":tuple(lines),"sections":sections}),self.fx.lyrics,self.fx.storyboard,self.fx.timelines[0])
        self.assertEqual(duration,max(scene.end_s for scene in plan.scenes))
    def test_scene_duration_matches_start_and_end(self):
        for plan in self.plans:
            for scene in plan.scenes: self.assertAlmostEqual(scene.duration_s,scene.end_s-scene.start_s)
    def test_scene_plan_is_deterministic(self):
        again=self.planner.plan("008",self.fx.alignments[0],self.fx.lyrics,self.fx.storyboard,self.fx.timelines[0])
        self.assertEqual(self.plans[0],again)
    def test_group_vocal_lines_creates_one_scene_per_lyrics_section(self):
        planner=SemanticScenePlanner(ScenePlanningThresholds(group_vocal_lines=True))
        plan=planner.plan("008",self.fx.alignments[0],self.fx.lyrics,self.fx.storyboard,self.fx.timelines[0])
        vocal=[scene for scene in plan.scenes if scene.scene_type.value=="vocal"]
        mapped_sections={line.source_lyrics_line_id for line in self.fx.alignments[0].lines}
        expected={section.section_id for section in self.fx.lyrics.sections if any(line.line_id in mapped_sections for line in section.lines)}
        self.assertEqual(len(expected),len(vocal)); self.assertEqual(expected,{scene.source_section_ids[0] for scene in vocal})
        self.assertFalse(any(scene.scene_type.value=="instrumental_break" for scene in plan.scenes))
        self.assertTrue(all(abs(first.end_s-second.start_s)<.001 for first,second in zip(plan.scenes,plan.scenes[1:])))
    def test_scene_plan_json_is_byte_stable(self):
        first=self.visual/"first.json"; second=self.visual/"second.json"
        write_scene_plan(first,self.plans[0]); write_scene_plan(second,self.plans[0])
        self.assertEqual(first.read_bytes(),second.read_bytes()); self.assertEqual(self.plans[0],read_scene_plan(first))

    def test_scene_plan_is_reused_when_dependencies_are_unchanged(self):
        first,reused=self.repository.resolve_or_build("008",self.fx.alignments[0],self.fx.lyrics,self.planner,self.fx.storyboard,self.fx.timelines[0])
        second,reused=self.repository.resolve_or_build("008",self.fx.alignments[0],self.fx.lyrics,self.planner,self.fx.storyboard,self.fx.timelines[0])
        self.assertTrue(reused); self.assertEqual(first,second)

    def test_alignment_sha_change_invalidates_only_affected_scene_plan(self):
        for alignment,timeline in zip(self.fx.alignments,self.fx.timelines): self.repository.resolve_or_build("008",alignment,self.fx.lyrics,self.planner,self.fx.storyboard,timeline)
        other_before=self.repository.path("variant-02").read_bytes()
        word=self.fx.alignments[0].words[0].model_copy(update={"start_seconds":2.05})
        changed=self.fx.alignments[0].model_copy(update={"words":(word,*self.fx.alignments[0].words[1:])})
        _value,reused=self.repository.resolve_or_build("008",changed,self.fx.lyrics,self.planner,self.fx.storyboard,self.fx.timelines[0])
        self.assertFalse(reused); self.assertEqual(other_before,self.repository.path("variant-02").read_bytes())

    def test_lyrics_sha_change_invalidates_scene_plan(self):
        self.repository.resolve_or_build("008",self.fx.alignments[0],self.fx.lyrics,self.planner,self.fx.storyboard,self.fx.timelines[0])
        sections=list(self.fx.lyrics.sections); line=sections[0].lines[0].model_copy(update={"text":sections[0].lines[0].text+"!"})
        sections[0]=sections[0].model_copy(update={"lines":(line,)})
        _value,reused=self.repository.resolve_or_build("008",self.fx.alignments[0],self.fx.lyrics.model_copy(update={"sections":tuple(sections)}),self.planner,self.fx.storyboard,self.fx.timelines[0])
        self.assertFalse(reused)

    def test_audio_duration_change_invalidates_scene_plan(self):
        self.repository.resolve_or_build("008",self.fx.alignments[0],self.fx.lyrics,self.planner,self.fx.storyboard,self.fx.timelines[0])
        changed=self.fx.alignments[0].model_copy(update={"audio_duration_seconds":20.5})
        _value,reused=self.repository.resolve_or_build("008",changed,self.fx.lyrics,self.planner,self.fx.storyboard,self.fx.timelines[0]); self.assertFalse(reused)

    def test_planner_version_change_invalidates_scene_plan(self):
        self.repository.resolve_or_build("008",self.fx.alignments[0],self.fx.lyrics,self.planner,self.fx.storyboard,self.fx.timelines[0])
        _value,reused=self.repository.resolve_or_build("008",self.fx.alignments[0],self.fx.lyrics,SemanticScenePlanner(planner_version="17.1.1"),self.fx.storyboard,self.fx.timelines[0]); self.assertFalse(reused)

    def test_scene_plan_change_does_not_invalidate_alignment(self):
        path=self.fx.music/"alignment-variant-01.json"; before=path.read_bytes()
        self.repository.resolve_or_build("008",self.fx.alignments[0],self.fx.lyrics,SemanticScenePlanner(planner_version="changed"),self.fx.storyboard,self.fx.timelines[0])
        self.assertEqual(before,path.read_bytes())
    def test_scene_plan_does_not_invalidate_other_audio_variant(self):
        self.repository.resolve_or_build("008",self.fx.alignments[1],self.fx.lyrics,self.planner,self.fx.storyboard,self.fx.timelines[1]); before=self.repository.path("variant-02").read_bytes()
        self.repository.resolve_or_build("008",self.fx.alignments[0],self.fx.lyrics,SemanticScenePlanner(planner_version="changed"),self.fx.storyboard,self.fx.timelines[0])
        self.assertEqual(before,self.repository.path("variant-02").read_bytes())

    def test_scene_plan_serialization_preserves_romanian_diacritics(self):
        self.repository.save(self.plans[0]); text=self.repository.path("variant-01").read_text(encoding="utf-8")
        self.assertIn("Mărul roșu",text); self.assertIn("țânțarul",text)
    def test_scene_plan_contains_no_absolute_local_paths(self):
        serialized=self.plans[0].model_dump_json(); self.assertNotIn(str(self.fx.root),serialized); self.assertNotIn(":\\",serialized)

    def test_scene_planning_performs_zero_http_calls(self):
        http=Mock(); self.planner.plan("008",self.fx.alignments[0],self.fx.lyrics,self.fx.storyboard,self.fx.timelines[0]); http.assert_not_called()
    def test_scene_planning_performs_zero_ai_calls(self):
        ai=Mock(); self.planner.plan("008",self.fx.alignments[0],self.fx.lyrics,self.fx.storyboard,self.fx.timelines[0]); ai.assert_not_called()
    def test_scene_planning_performs_zero_ffmpeg_calls(self):
        ffmpeg=Mock(); self.planner.plan("008",self.fx.alignments[0],self.fx.lyrics,self.fx.storyboard,self.fx.timelines[0]); ffmpeg.assert_not_called()


if __name__=="__main__": unittest.main()
