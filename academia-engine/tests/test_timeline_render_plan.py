import json
from pathlib import Path
import unittest

from app.media import MediaProbeResult
from app.timeline import (
    TimelineRenderPlan,
    TimelineRenderPlanDurationError,
    TimelineRenderPlanInvariantError,
    TimelineTransition,
    ValidatedTimelineScene,
    ValidatedVideoTimeline,
    build_render_plan,
)


class TimelineRenderPlanTests(unittest.TestCase):
    def test_two_and_multiple_cut_scenes_have_adjacent_positions_and_no_transitions(self) -> None:
        two = validated([vscene("one", 0, 0, 5, audio=True, transition=TimelineTransition(kind="cut")), vscene("two", 1, 2, 9, audio=False)], total=12)
        plan = build_render_plan(two)
        self.assertEqual([(item.output_start_seconds, item.output_end_seconds) for item in plan.scenes], [(0, 5), (5, 12)])
        self.assertEqual(plan.transitions, ())
        self.assertEqual([item.input_index for item in plan.scenes], [0, 1])
        self.assertEqual([item.has_audio for item in plan.scenes], [True, False])

        multi = validated([vscene("one", 0, 0, 2, transition=TimelineTransition(kind="cut")), vscene("two", 1, 0, 3, transition=TimelineTransition(kind="cut", duration_seconds=0)), vscene("three", 2, 0, 4)], total=9)
        self.assertEqual([(item.output_start_seconds, item.output_end_seconds) for item in build_render_plan(multi).scenes], [(0, 2), (2, 5), (5, 9)])

    def test_fade_and_dissolve_positions_and_transition_ranges(self) -> None:
        for kind in ("fade", "dissolve"):
            with self.subTest(kind=kind):
                value = validated([vscene("one", 0, 1, 7, transition=TimelineTransition(kind=kind, duration_seconds=2)), vscene("two", 1, 3, 8)], total=9)
                plan = build_render_plan(value)
                first, second = plan.scenes
                transition = plan.transitions[0]
                self.assertEqual((first.output_start_seconds, first.output_end_seconds), (0, 6))
                self.assertEqual((second.output_start_seconds, second.output_end_seconds), (4, 9))
                self.assertEqual((transition.start_seconds, transition.end_seconds), (4, 6))
                self.assertEqual(second.output_start_seconds, transition.start_seconds)
                self.assertEqual(transition.end_seconds, first.output_end_seconds)
                self.assertEqual(transition.end_seconds - transition.start_seconds, transition.duration_seconds)
                self.assertEqual((second.source_start_seconds, second.source_end_seconds), (3, 8))

    def test_multiple_overlaps_preserve_order_indexes_and_final_duration(self) -> None:
        value = validated([
            vscene("third-input-name", 4, 0, 10, transition=TimelineTransition(kind="fade", duration_seconds=1)),
            vscene("first-looking-name", 8, 2, 10, transition=TimelineTransition(kind="dissolve", duration_seconds=2)),
            vscene("last", 12, 0, 6),
        ], total=21)
        plan = build_render_plan(value)
        self.assertEqual([item.scene_id for item in plan.scenes], ["third-input-name", "first-looking-name", "last"])
        self.assertEqual([item.input_index for item in plan.scenes], [0, 1, 2])
        self.assertEqual([(item.output_start_seconds, item.output_end_seconds) for item in plan.scenes], [(0, 10), (9, 17), (15, 21)])
        self.assertEqual([(item.start_seconds, item.end_seconds) for item in plan.transitions], [(9, 10), (15, 17)])
        self.assertEqual(plan.scenes[-1].output_end_seconds, value.total_duration_seconds)
        self.assertEqual(plan.expected_duration_seconds, value.total_duration_seconds)
        self.assertEqual(plan.destination, value.destination)
        self.assertEqual(plan.workspace, value.workspace)

    def test_exact_source_trim_ranges_are_preserved(self) -> None:
        value = validated([vscene("one", 0, 1.25, 4.75, transition=TimelineTransition(kind="cut")), vscene("two", 1, 0.5, 2.5)], total=5.5)
        plan = build_render_plan(value)
        self.assertEqual((plan.scenes[0].source_start_seconds, plan.scenes[0].source_end_seconds), (1.25, 4.75))
        self.assertEqual((plan.scenes[1].source_start_seconds, plan.scenes[1].source_end_seconds), (0.5, 2.5))

    def test_duration_mismatch_is_rejected(self) -> None:
        value = validated([vscene("one", 0, 0, 5, transition=TimelineTransition(kind="cut")), vscene("two", 1, 0, 5)], total=11)
        with self.assertRaises(TimelineRenderPlanDurationError):
            build_render_plan(value)

    def test_invalid_scene_timing_is_rejected_explicitly(self) -> None:
        invalid = ValidatedTimelineScene.model_construct(scene_id="bad", source_path=Path("bad.mp4"), order=0, source_media_info=info("bad.mp4", True), effective_start_seconds=4.0, effective_end_seconds=3.0, effective_duration_seconds=-1.0, transition_to_next=TimelineTransition(kind="cut"))
        good = vscene("good", 1, 0, 2)
        value = ValidatedVideoTimeline.model_construct(timeline_id="timeline-01", scenes=(invalid, good), destination=Path("final.mp4"), workspace=Path("work"), source_count=2, total_duration_seconds=1)
        with self.assertRaises(TimelineRenderPlanInvariantError):
            build_render_plan(value)

    def test_plan_is_deterministic_serializable_and_provider_neutral(self) -> None:
        value = validated([vscene("one", 0, 0, 5, transition=TimelineTransition(kind="fade", duration_seconds=1)), vscene("two", 1, 0, 5)], total=9)
        first = build_render_plan(value)
        second = build_render_plan(value)
        self.assertEqual(first, second)
        serialized = first.to_json()
        self.assertEqual(serialized, first.to_json())
        self.assertEqual(TimelineRenderPlan.from_json(serialized), first)
        payload = json.loads(serialized)
        self.assertEqual(list(payload), ["timeline_id", "scenes", "transitions", "destination", "workspace", "expected_duration_seconds"])
        for forbidden in ("ffmpeg", "filter_complex", "setpts", "xfade", "acrossfade", "provider", "kling", "signed", "authorization", "task_id"):
            self.assertNotIn(forbidden, serialized.lower())


def info(path, audio):
    return MediaProbeResult(local_path=Path(path), duration_seconds=20, width=1280, height=720, frame_rate=30, video_codec="h264", audio_codec="aac" if audio else None, has_audio=audio, container_format="mp4")


def vscene(scene_id, order, start, end, audio=True, transition=None):
    return ValidatedTimelineScene(scene_id=scene_id, source_path=Path(f"{scene_id}.mp4"), order=order, source_media_info=info(f"{scene_id}.mp4", audio), effective_start_seconds=start, effective_end_seconds=end, effective_duration_seconds=end - start, transition_to_next=transition)


def validated(scenes, total):
    return ValidatedVideoTimeline(timeline_id="timeline-01", scenes=tuple(scenes), destination=Path("final.mp4"), workspace=Path("work"), source_count=len(scenes), total_duration_seconds=total)
