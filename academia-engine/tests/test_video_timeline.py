import json
from pathlib import Path
import unittest

from pydantic import ValidationError

from app.composition import VideoCompositionManifest, VideoCompositionOutput, VideoCompositionScene
from app.timeline import (
    TimelineOutput,
    TimelineScene,
    TimelineTransition,
    TimelineTransitionKind,
    VideoTimeline,
    resolve_timeline,
    timeline_from_composition_manifest,
)


class VideoTimelineTests(unittest.TestCase):
    def test_valid_cuts_fade_and_dissolve(self) -> None:
        transitions = [
            TimelineTransition(kind="cut"),
            TimelineTransition(kind="fade", duration_seconds=0.5),
            TimelineTransition(kind="dissolve", duration_seconds=1.25),
        ]
        for transition in transitions:
            with self.subTest(transition=transition):
                value = timeline([scene("one", "one.mp4", 0, transition=transition), scene("two", "two.mp4", 1)])
                self.assertEqual(resolve_timeline(value).ordered_scenes[0].transition_to_next, transition)

    def test_arbitrary_input_order_resolves_ascending_without_mutation(self) -> None:
        value = timeline([scene("third", "three.mp4", 8), scene("first", "one.mp4", 1), scene("second", "two.mp4", 4)])
        before = value.to_json()
        resolved = resolve_timeline(value)
        self.assertEqual([item.scene_id for item in resolved.ordered_scenes], ["first", "second", "third"])
        self.assertEqual(value.to_json(), before)
        self.assertEqual([item.scene_id for item in value.scenes], ["third", "first", "second"])

    def test_duplicate_ids_orders_invalid_identity_order_and_counts_are_rejected(self) -> None:
        invalid_scene_sets = [
            [scene("same", "one.mp4", 0), scene("same", "two.mp4", 1)],
            [scene("one", "one.mp4", 0), scene("two", "two.mp4", 0)],
            [],
            [scene("one", "one.mp4", 0)],
        ]
        for scenes in invalid_scene_sets:
            with self.subTest(scenes=scenes), self.assertRaises(ValidationError):
                timeline(scenes)
        for values in [
            {"scene_id": "", "source_path": "one.mp4", "order": 0},
            {"scene_id": "   ", "source_path": "one.mp4", "order": 0},
            {"scene_id": "one", "source_path": "one.mp4", "order": -1},
        ]:
            with self.assertRaises(ValidationError):
                TimelineScene(**values)

    def test_invalid_trim_ranges_are_rejected_without_media_inspection(self) -> None:
        invalid = [
            {"trim_start_seconds": -0.1},
            {"trim_end_seconds": 0},
            {"trim_end_seconds": -1},
            {"trim_start_seconds": 2, "trim_end_seconds": 2},
            {"trim_start_seconds": 3, "trim_end_seconds": 2},
        ]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                TimelineScene(scene_id="one", source_path="missing-local-file.mp4", order=0, **changes)

    def test_transition_vocabulary_and_duration_semantics_are_strict(self) -> None:
        invalid = [
            {"kind": "wipe", "duration_seconds": 1},
            {"kind": "cut", "duration_seconds": 1},
            {"kind": "fade"},
            {"kind": "fade", "duration_seconds": 0},
            {"kind": "dissolve"},
            {"kind": "dissolve", "duration_seconds": -1},
        ]
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                TimelineTransition(**values)

    def test_last_resolved_scene_cannot_define_transition(self) -> None:
        with self.assertRaises(ValidationError):
            timeline([scene("last", "last.mp4", 10, transition=TimelineTransition(kind="cut")), scene("first", "first.mp4", 0)])

    def test_omitted_non_last_transition_normalizes_to_cut(self) -> None:
        resolved = resolve_timeline(timeline([scene("one", "one.mp4", 0), scene("two", "two.mp4", 1)]))
        transition = resolved.ordered_scenes[0].transition_to_next
        self.assertEqual(transition.kind, TimelineTransitionKind.CUT)
        self.assertEqual(transition.duration_seconds, 0)
        self.assertIsNone(resolved.ordered_scenes[-1].transition_to_next)

    def test_remote_signed_and_invalid_output_paths_are_rejected(self) -> None:
        for path in ["http://example/video.mp4", "https://example/video.mp4", "video.mp4?signature=secret"]:
            with self.subTest(path=path), self.assertRaises(ValidationError):
                scene("one", path, 0)
        for values in [
            {"destination": "https://example/final.mp4", "workspace": "work"},
            {"destination": "final.mp4", "workspace": "http://example/work"},
            {"destination": "final.mp4", "workspace": ".\\final.mp4"},
        ]:
            with self.assertRaises(ValidationError):
                TimelineOutput(**values)

    def test_serialization_is_deterministic_semantic_and_round_trips(self) -> None:
        value = timeline([scene("one", "one.mp4", 0, start=1, end=4, transition=TimelineTransition(kind="fade", duration_seconds=0.5)), scene("two", "two.mp4", 1)])
        serialized = value.to_json()
        self.assertEqual(serialized, value.to_json())
        self.assertEqual(VideoTimeline.from_json(serialized), value)
        payload = json.loads(serialized)
        self.assertEqual(list(payload), ["timeline_id", "scenes", "output"])
        self.assertEqual(payload["scenes"][0]["transition_to_next"]["kind"], "fade")
        for forbidden in ("ffmpeg", "filter_complex", "xfade", "kling", "provider", "signed", "authorization", "task_id"):
            self.assertNotIn(forbidden, serialized.lower())

    def test_composition_bridge_preserves_semantics_and_adds_only_cuts(self) -> None:
        manifest = VideoCompositionManifest(composition_id="episode-01", scenes=(VideoCompositionScene(scene_id="second", source_path="two.mp4", order=5), VideoCompositionScene(scene_id="first", source_path="one.mp4", order=1)), output=VideoCompositionOutput(destination="final.mp4", workspace="work"))
        value = timeline_from_composition_manifest(manifest)
        resolved = resolve_timeline(value)
        self.assertEqual(value.timeline_id, manifest.composition_id)
        self.assertEqual([(item.scene_id, item.source_path, item.order) for item in value.scenes], [("second", Path("two.mp4"), 5), ("first", Path("one.mp4"), 1)])
        self.assertTrue(all(item.trim_start_seconds is None and item.trim_end_seconds is None for item in value.scenes))
        self.assertEqual([item.source_path for item in resolved.ordered_scenes], [Path("one.mp4"), Path("two.mp4")])
        self.assertEqual(resolved.ordered_scenes[0].transition_to_next.kind, TimelineTransitionKind.CUT)
        self.assertIsNone(resolved.ordered_scenes[-1].transition_to_next)
        self.assertEqual(value.output.destination, manifest.output.destination)
        self.assertEqual(value.output.workspace, manifest.output.workspace)


def scene(scene_id, source_path, order, start=None, end=None, transition=None):
    return TimelineScene(scene_id=scene_id, source_path=source_path, order=order, trim_start_seconds=start, trim_end_seconds=end, transition_to_next=transition)


def timeline(scenes):
    return VideoTimeline(timeline_id="timeline-01", scenes=tuple(scenes), output=TimelineOutput(destination="final.mp4", workspace="work"))
