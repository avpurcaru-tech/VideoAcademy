from pathlib import Path
import unittest

from app.timeline import (
    FFmpegTimelineAudioCompatibilityError,
    FFmpegTimelineInputError,
    TimelineTransition,
    compile_ffmpeg_timeline,
)
from tests.test_timeline_render_plan import validated, vscene
from app.timeline import build_render_plan


class FFmpegTimelineCompilerTests(unittest.TestCase):
    def test_two_scene_cut_video_only_has_exact_filters_maps_and_input_order(self) -> None:
        plan = build_render_plan(validated([vscene("one", 0, 1, 5, audio=False, transition=TimelineTransition(kind="cut")), vscene("two", 1, 2, 8, audio=False)], total=10))
        before = plan.to_json()
        command = compile_ffmpeg_timeline(plan)
        self.assertEqual(command.filter_complex, "[0:v:0]trim=start=1:end=5,setpts=PTS-STARTPTS[v0];[1:v:0]trim=start=2:end=8,setpts=PTS-STARTPTS[v1];[v0][v1]concat=n=2:v=1:a=0[vout]")
        self.assertEqual(command.args[:6], ("ffmpeg", "-n", "-i", "one.mp4", "-i", "two.mp4"))
        self.assertEqual(command.args.count("-map"), 1)
        self.assertIn("[vout]", command.args)
        self.assertNotIn("[aout]", command.args)
        self.assertFalse(command.has_audio_output)
        self.assertEqual(command.expected_output_path, plan.destination)
        self.assertEqual(command.args[-1], str(plan.destination))
        self.assertEqual(plan.to_json(), before)

    def test_two_scene_cut_with_audio_prepares_and_maps_audio(self) -> None:
        plan = build_render_plan(validated([vscene("one", 0, 0, 5, transition=TimelineTransition(kind="cut")), vscene("two", 1, 0, 5)], total=10))
        command = compile_ffmpeg_timeline(plan)
        self.assertIn("[0:a:0]atrim=start=0:end=5,asetpts=PTS-STARTPTS[a0]", command.filter_complex)
        self.assertIn("[1:a:0]atrim=start=0:end=5,asetpts=PTS-STARTPTS[a1]", command.filter_complex)
        self.assertIn("[a0][a1]concat=n=2:v=0:a=1[aout]", command.filter_complex)
        self.assertEqual(command.args.count("-map"), 2)
        self.assertIn("[aout]", command.args)
        self.assertIn("-c:a", command.args)
        self.assertTrue(command.has_audio_output)

    def test_multiple_cuts_use_one_deterministic_concat_per_stream(self) -> None:
        plan = build_render_plan(validated([vscene("one", 0, 0, 2, transition=TimelineTransition(kind="cut")), vscene("two", 1, 0, 3, transition=TimelineTransition(kind="cut")), vscene("three", 2, 0, 4)], total=9))
        graph = compile_ffmpeg_timeline(plan).filter_complex
        self.assertIn("[v0][v1][v2]concat=n=3:v=1:a=0[vout]", graph)
        self.assertIn("[a0][a1][a2]concat=n=3:v=0:a=1[aout]", graph)

    def test_fade_and_dissolve_map_to_xfade_and_acrossfade(self) -> None:
        for kind in ("fade", "dissolve"):
            with self.subTest(kind=kind):
                plan = build_render_plan(validated([vscene("one", 0, 0, 6, transition=TimelineTransition(kind=kind, duration_seconds=1.25)), vscene("two", 1, 0, 5)], total=9.75))
                graph = compile_ffmpeg_timeline(plan).filter_complex
                self.assertIn(f"[v0][v1]xfade=transition={kind}:duration=1.25:offset=4.75[vout]", graph)
                self.assertIn("[a0][a1]acrossfade=d=1.25:c1=tri:c2=tri[aout]", graph)

    def test_multiple_overlaps_have_exact_offsets_and_deterministic_labels(self) -> None:
        plan = build_render_plan(validated([vscene("one", 0, 0, 10, transition=TimelineTransition(kind="fade", duration_seconds=1)), vscene("two", 1, 0, 8, transition=TimelineTransition(kind="dissolve", duration_seconds=2)), vscene("three", 2, 0, 6)], total=21))
        graph = compile_ffmpeg_timeline(plan).filter_complex
        self.assertIn("[v0][v1]xfade=transition=fade:duration=1:offset=9[vx1]", graph)
        self.assertIn("[vx1][v2]xfade=transition=dissolve:duration=2:offset=15[vout]", graph)
        self.assertIn("[a0][a1]acrossfade=d=1:c1=tri:c2=tri[ax1]", graph)
        self.assertIn("[ax1][a2]acrossfade=d=2:c1=tri:c2=tri[aout]", graph)

    def test_mixed_cut_fade_chain_compiles_explicit_sequential_graph(self) -> None:
        plan = build_render_plan(validated([vscene("one", 0, 0, 4, transition=TimelineTransition(kind="cut")), vscene("two", 1, 0, 6, transition=TimelineTransition(kind="fade", duration_seconds=1)), vscene("three", 2, 0, 5)], total=14))
        graph = compile_ffmpeg_timeline(plan).filter_complex
        self.assertIn("[v0][v1]concat=n=2:v=1:a=0[vx1]", graph)
        self.assertIn("[vx1][v2]xfade=transition=fade:duration=1:offset=9[vout]", graph)
        self.assertIn("[a0][a1]concat=n=2:v=0:a=1[ax1]", graph)
        self.assertIn("[ax1][a2]acrossfade=d=1:c1=tri:c2=tri[aout]", graph)

    def test_numeric_formatting_is_stable_bounded_and_locale_independent(self) -> None:
        plan = build_render_plan(validated([vscene("one", 0, 0.1234567894, 3.1234567894, audio=False, transition=TimelineTransition(kind="cut")), vscene("two", 1, 0, 2, audio=False)], total=5))
        graph = compile_ffmpeg_timeline(plan).filter_complex
        self.assertIn("trim=start=0.123456789:end=3.123456789", graph)
        self.assertNotIn("e-", graph.lower())
        self.assertNotIn("start=0,123", graph)

    def test_mixed_audio_presence_is_rejected_for_even_cut_only_timeline(self) -> None:
        plan = build_render_plan(validated([vscene("one", 0, 0, 5, audio=True, transition=TimelineTransition(kind="cut")), vscene("two", 1, 0, 5, audio=False)], total=10))
        with self.assertRaises(FFmpegTimelineAudioCompatibilityError):
            compile_ffmpeg_timeline(plan)

    def test_noncontiguous_or_tuple_mismatched_indexes_are_rejected(self) -> None:
        plan = build_render_plan(validated([vscene("one", 0, 0, 5, audio=False, transition=TimelineTransition(kind="cut")), vscene("two", 1, 0, 5, audio=False)], total=10))
        invalid_scene = plan.scenes[1].model_copy(update={"input_index": 3})
        invalid = plan.model_copy(update={"scenes": (plan.scenes[0], invalid_scene)})
        with self.assertRaises(FFmpegTimelineInputError):
            compile_ffmpeg_timeline(invalid)

    def test_overwrite_flags_profile_settings_and_command_safety_are_explicit(self) -> None:
        plan = build_render_plan(validated([vscene("one", 0, 0, 5, audio=False, transition=TimelineTransition(kind="cut")), vscene("two", 1, 0, 5, audio=False)], total=10))
        safe = compile_ffmpeg_timeline(plan)
        overwrite = compile_ffmpeg_timeline(plan, overwrite=True)
        self.assertEqual(safe.args[1], "-n")
        self.assertEqual(overwrite.args[1], "-y")
        self.assertIn("libx264", safe.args)
        self.assertIn("yuv420p", safe.args)
        self.assertIn("30", safe.args)
        self.assertNotIn("shell=True", safe.args)
        self.assertFalse(any(value in {"|", "&&", ";"} for value in safe.args))
        self.assertNotIn("one.mp4", safe.filter_complex)
        self.assertNotIn("two.mp4", safe.filter_complex)
        input_values = [safe.args[index + 1] for index, value in enumerate(safe.args[:-1]) if value == "-i"]
        self.assertEqual(input_values, ["one.mp4", "two.mp4"])
