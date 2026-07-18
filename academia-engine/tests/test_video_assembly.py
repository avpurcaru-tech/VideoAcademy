from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.media import (
    AssembledVideoArtifact,
    AssemblyConcatenationError,
    AssemblyDestinationExistsError,
    AssemblyLoudnessNormalizationError,
    AssemblySceneNormalizationError,
    AssemblySourceValidationError,
    AudioLoudnessProfile,
    ConcatenatedVideoArtifact,
    LoudnessNormalizedVideoArtifact,
    MediaProbeResult,
    NormalizedVideoArtifact,
    VideoAssemblyRequest,
    VideoAssemblyService,
    VideoNormalizationProfile,
)


class VideoAssemblyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        self.sources = [self.root / "scene-b.mp4", self.root / "scene-a.mp4", self.root / "scene-c.mp4"]
        for source in self.sources:
            source.write_bytes(b"source")
        self.normalizer = FakeNormalizer()
        self.concatenator = FakeConcatenator()
        self.loudness = FakeLoudnessNormalizer()
        self.service = VideoAssemblyService(self.normalizer, self.concatenator, self.loudness)

    def test_successful_two_scene_assembly_preserves_order_and_metadata(self) -> None:
        request = self._request(self.sources[:2])
        result = self.service.assemble(request)

        self.assertEqual([call[0] for call in self.normalizer.calls], self.sources[:2])
        self.assertEqual(len(self.normalizer.calls), 2)
        self.assertEqual(len(self.concatenator.calls), 1)
        self.assertEqual(len(self.loudness.calls), 1)
        normalized = self.concatenator.calls[0][0]
        self.assertEqual([path.name for path in normalized], ["scene_0001.normalized.mp4", "scene_0002.normalized.mp4"])
        self.assertEqual(self.loudness.calls[0][0].name, "concatenated.mp4")
        self.assertEqual(result.local_path, request.destination)
        self.assertEqual(result.byte_size, self.loudness.artifact.byte_size)
        self.assertEqual(result.sha256, self.loudness.artifact.sha256)
        self.assertEqual(result.media_info, self.loudness.artifact.media_info)
        self.assertEqual(result.source_count, 2)
        self.assertNotIn("scene_0001", str(result.model_dump()))
        self.assertTrue(request.destination.is_file())
        self.assertTrue(self.workspace.is_dir())
        self.assertEqual(list(self.workspace.iterdir()), [])
        for source in self.sources:
            self.assertTrue(source.is_file())

    def test_successful_multi_scene_assembly_normalizes_each_once(self) -> None:
        result = self.service.assemble(self._request(self.sources))
        self.assertEqual(result.source_count, 3)
        self.assertEqual(len(self.normalizer.calls), 3)
        self.assertEqual([path.name for path in self.concatenator.calls[0][0]], ["scene_0001.normalized.mp4", "scene_0002.normalized.mp4", "scene_0003.normalized.mp4"])

    def test_zero_one_and_missing_sources_fail_before_workspace(self) -> None:
        cases = [[], self.sources[:1], [self.sources[0], self.root / "missing.mp4"]]
        for sources in cases:
            with self.subTest(sources=sources), self.assertRaises(AssemblySourceValidationError):
                self.service.assemble(self._request(sources))
        self.assertEqual(self.normalizer.calls, [])
        self.assertFalse(self.workspace.exists())

    def test_existing_destination_fails_before_expensive_work(self) -> None:
        request = self._request(self.sources[:2])
        request.destination.write_bytes(b"existing")
        with self.assertRaises(AssemblyDestinationExistsError):
            self.service.assemble(request)
        self.assertEqual(request.destination.read_bytes(), b"existing")
        self.assertEqual(self.normalizer.calls, [])
        self.assertFalse(self.workspace.exists())

    def test_overwrite_is_propagated_only_to_final_publication(self) -> None:
        request = self._request(self.sources[:2], overwrite=True)
        request.destination.write_bytes(b"existing")
        self.service.assemble(request)
        self.assertTrue(self.loudness.calls[0][3])
        self.assertEqual(request.destination.read_bytes(), b"final")

    def test_normalization_failure_stops_and_cleans_isolated_directory(self) -> None:
        self.normalizer.failure_at = 2
        request = self._request(self.sources[:2])
        with self.assertRaises(AssemblySceneNormalizationError):
            self.service.assemble(request)
        self.assertEqual(len(self.normalizer.calls), 2)
        self.assertEqual(self.concatenator.calls, [])
        self.assertEqual(self.loudness.calls, [])
        self._assert_failure_cleanup(request)

    def test_concatenation_failure_stops_and_cleans_isolated_directory(self) -> None:
        self.concatenator.error = RuntimeError("safe concat failure")
        request = self._request(self.sources[:2])
        with self.assertRaises(AssemblyConcatenationError):
            self.service.assemble(request)
        self.assertEqual(len(self.concatenator.calls), 1)
        self.assertEqual(self.loudness.calls, [])
        self._assert_failure_cleanup(request)

    def test_loudness_failure_leaves_no_destination_and_cleans(self) -> None:
        self.loudness.error = RuntimeError("safe loudness failure")
        request = self._request(self.sources[:2])
        with self.assertRaises(AssemblyLoudnessNormalizationError):
            self.service.assemble(request)
        self.assertEqual(len(self.loudness.calls), 1)
        self._assert_failure_cleanup(request)

    def test_each_invocation_uses_a_different_isolated_directory(self) -> None:
        self.service.assemble(self._request(self.sources[:2], destination=self.root / "one.mp4"))
        self.service.assemble(self._request(self.sources[:2], destination=self.root / "two.mp4"))
        first_parent = self.normalizer.calls[0][1].parent
        second_parent = self.normalizer.calls[2][1].parent
        self.assertNotEqual(first_parent, second_parent)
        self.assertEqual(first_parent.parent.resolve(), self.workspace.resolve())
        self.assertEqual(second_parent.parent.resolve(), self.workspace.resolve())
        self.assertFalse(first_parent.exists())
        self.assertFalse(second_parent.exists())

    def _request(self, sources, overwrite=False, destination=None):
        return VideoAssemblyRequest(sources=tuple(sources), destination=destination or self.root / "final.mp4", workspace=self.workspace, normalization_profile=VideoNormalizationProfile.academia_default(), loudness_profile=AudioLoudnessProfile.academia_default(), overwrite=overwrite)

    def _assert_failure_cleanup(self, request):
        self.assertFalse(request.destination.exists())
        self.assertTrue(self.workspace.is_dir())
        self.assertEqual(list(self.workspace.iterdir()), [])
        for source in self.sources:
            self.assertTrue(source.is_file())


class FakeNormalizer:
    def __init__(self):
        self.calls = []
        self.failure_at = None

    def normalize_video(self, source, destination, profile):
        self.calls.append((source, destination, profile))
        if self.failure_at == len(self.calls):
            raise RuntimeError("normalization failed")
        destination.write_bytes(b"normalized")
        return NormalizedVideoArtifact(local_path=destination, byte_size=10, sha256="a" * 64, media_info=media(destination))


class FakeConcatenator:
    def __init__(self):
        self.calls = []
        self.error = None

    def concatenate_videos(self, sources, destination):
        self.calls.append((list(sources), destination))
        if self.error:
            raise self.error
        destination.write_bytes(b"concatenated")
        return ConcatenatedVideoArtifact(local_path=destination, byte_size=12, sha256="b" * 64, media_info=media(destination), source_count=len(sources))


class FakeLoudnessNormalizer:
    def __init__(self):
        self.calls = []
        self.error = None
        self.artifact = None

    def normalize_loudness(self, source, destination, profile, *, overwrite=False):
        self.calls.append((source, destination, profile, overwrite))
        if self.error:
            raise self.error
        destination.write_bytes(b"final")
        self.artifact = LoudnessNormalizedVideoArtifact(local_path=destination, byte_size=5, sha256="c" * 64, media_info=media(destination))
        return self.artifact


def media(path):
    return MediaProbeResult(local_path=path, duration_seconds=20, width=1280, height=720, frame_rate=30, video_codec="h264", audio_codec="aac", has_audio=True, container_format="mp4")
