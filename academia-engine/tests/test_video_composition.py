import json
from pathlib import Path
import unittest

from pydantic import ValidationError

from app.composition import (
    VideoCompositionManifest,
    VideoCompositionOutput,
    VideoCompositionScene,
    resolve_manifest,
    to_assembly_request,
)
from app.media import AudioLoudnessProfile, VideoNormalizationProfile


class VideoCompositionContractTests(unittest.TestCase):
    def test_valid_two_and_multi_scene_manifests_resolve_arbitrary_input_order(self) -> None:
        two = manifest([scene("second", "two.mp4", 20), scene("first", "one.mp4", 10)])
        resolved = resolve_manifest(two)
        self.assertEqual(resolved.composition_id, "episode-01")
        self.assertEqual(resolved.ordered_sources, (Path("one.mp4"), Path("two.mp4")))
        self.assertEqual(resolved.source_count, 2)

        multi = manifest([scene("third", "three.mp4", 3), scene("first", "one.mp4", 0), scene("second", "two.mp4", 2)])
        self.assertEqual(resolve_manifest(multi).ordered_sources, (Path("one.mp4"), Path("two.mp4"), Path("three.mp4")))

    def test_duplicate_scene_id_and_order_are_rejected(self) -> None:
        cases = [
            [scene("same", "one.mp4", 0), scene("same", "two.mp4", 1)],
            [scene("one", "one.mp4", 0), scene("two", "two.mp4", 0)],
        ]
        for scenes in cases:
            with self.subTest(scenes=scenes), self.assertRaises(ValidationError):
                manifest(scenes)

    def test_invalid_scene_identity_order_and_count_are_rejected(self) -> None:
        for values in [
            {"scene_id": "", "source_path": "one.mp4", "order": 0},
            {"scene_id": "   ", "source_path": "one.mp4", "order": 0},
            {"scene_id": "one", "source_path": "one.mp4", "order": -1},
            {"scene_id": "one", "source_path": "one.mp4", "order": "1"},
        ]:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                VideoCompositionScene(**values)
        for scenes in ([], [scene("one", "one.mp4", 0)]):
            with self.assertRaises(ValidationError):
                manifest(scenes)

    def test_remote_url_scheme_and_signed_path_data_are_rejected(self) -> None:
        invalid = [
            "http://cdn.example/video.mp4",
            "https://cdn.example/video.mp4",
            "s3://bucket/video.mp4",
            "kling://task/video",
            "video.mp4?X-Amz-Signature=secret",
        ]
        for source_path in invalid:
            with self.subTest(source_path=source_path), self.assertRaises(ValidationError):
                scene("one", source_path, 0)

    def test_output_paths_are_local_explicit_and_different(self) -> None:
        invalid = [
            {"destination": "https://example/final.mp4", "workspace": "work"},
            {"destination": "final.mp4", "workspace": "http://example/work"},
            {"destination": "final.mp4?signature=x", "workspace": "work"},
            {"destination": "final.mp4", "workspace": ".\\final.mp4"},
            {"destination": "", "workspace": "work"},
        ]
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                VideoCompositionOutput(**values)

    def test_resolver_does_not_mutate_manifest(self) -> None:
        value = manifest([scene("second", "two.mp4", 2), scene("first", "one.mp4", 1)])
        before = value.to_json()
        resolve_manifest(value)
        self.assertEqual(value.to_json(), before)
        self.assertEqual([item.scene_id for item in value.scenes], ["second", "first"])

    def test_assembly_bridge_preserves_sources_profiles_and_overwrite(self) -> None:
        resolved = resolve_manifest(manifest([scene("two", "two.mp4", 9), scene("one", "one.mp4", 2)]))
        normalization = VideoNormalizationProfile(width=1920, height=1080, frame_rate=24, video_codec="libx264", audio_codec="aac", pixel_format="yuv420p")
        loudness = AudioLoudnessProfile(integrated_lufs=-14, loudness_range_lu=8, true_peak_db=-2)
        request = to_assembly_request(resolved, normalization, loudness, overwrite=True)
        self.assertEqual(request.sources, (Path("one.mp4"), Path("two.mp4")))
        self.assertIs(request.normalization_profile, normalization)
        self.assertIs(request.loudness_profile, loudness)
        self.assertTrue(request.overwrite)
        self.assertEqual(request.destination, resolved.destination)
        self.assertEqual(request.workspace, resolved.workspace)

    def test_serialization_is_deterministic_provider_neutral_and_round_trips(self) -> None:
        value = manifest([scene("two", "two.mp4", 2), scene("one", "one.mp4", 1)])
        first = value.to_json()
        second = value.to_json()
        self.assertEqual(first, second)
        self.assertEqual(VideoCompositionManifest.from_json(first), value)
        payload = json.loads(first)
        self.assertEqual(list(payload), ["composition_id", "scenes", "output"])
        serialized = first.lower()
        for forbidden in ("kling", "provider", "signed", "authorization", "api_key", "ffmpeg", "task_id", "url"):
            self.assertNotIn(forbidden, serialized)


def scene(scene_id, source_path, order):
    return VideoCompositionScene(scene_id=scene_id, source_path=source_path, order=order)


def manifest(scenes):
    return VideoCompositionManifest(composition_id="episode-01", scenes=tuple(scenes), output=VideoCompositionOutput(destination="final.mp4", workspace=".runtime/compositions/work"))
