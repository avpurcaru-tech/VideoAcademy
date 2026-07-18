from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.composition import (
    CompositionAssemblyError,
    CompositionDestinationConflictError,
    CompositionExecutionService,
    CompositionSourceValidationError,
    VideoCompositionManifest,
    VideoCompositionOutput,
    VideoCompositionScene,
)
from app.composition.resolver import resolve_manifest
from app.media import (
    AssembledVideoArtifact,
    AudioLoudnessProfile,
    MediaProbeResult,
    VideoNormalizationProfile,
)


class CompositionExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.first = self.root / "first.mp4"
        self.second = self.root / "second.mp4"
        self.first.write_bytes(b"first")
        self.second.write_bytes(b"second")
        self.assembly = FakeAssemblyService()
        self.service = CompositionExecutionService(self.assembly)

    def test_success_resolves_once_uses_defaults_calls_assembly_once_and_copies_metadata(self) -> None:
        value = self._manifest([
            VideoCompositionScene(scene_id="second", source_path=self.second, order=9),
            VideoCompositionScene(scene_id="first", source_path=self.first, order=2),
        ])
        with patch("app.composition.executor.resolve_manifest", wraps=resolve_manifest) as resolver:
            result = self.service.execute(value)

        resolver.assert_called_once_with(value)
        self.assertEqual(len(self.assembly.calls), 1)
        request = self.assembly.calls[0]
        self.assertEqual(request.sources, (self.first, self.second))
        self.assertEqual(request.normalization_profile, VideoNormalizationProfile.academia_default())
        self.assertEqual(request.loudness_profile, AudioLoudnessProfile.academia_default())
        self.assertFalse(request.overwrite)
        self.assertEqual(result.composition_id, "composition-01")
        self.assertEqual(result.local_path, self.assembly.artifact.local_path)
        self.assertEqual(result.byte_size, self.assembly.artifact.byte_size)
        self.assertEqual(result.sha256, self.assembly.artifact.sha256)
        self.assertEqual(result.media_info, self.assembly.artifact.media_info)
        self.assertEqual(result.source_count, self.assembly.artifact.source_count)

    def test_explicit_profiles_and_overwrite_are_propagated(self) -> None:
        normalization = VideoNormalizationProfile(width=1920, height=1080, frame_rate=24, video_codec="libx264", audio_codec="aac", pixel_format="yuv420p")
        loudness = AudioLoudnessProfile(integrated_lufs=-14, loudness_range_lu=8, true_peak_db=-2)
        destination = self.root / "final.mp4"
        destination.write_bytes(b"existing")
        value = self._manifest(self._scenes(), destination)
        self.assembly.expected_existing = b"existing"

        self.service.execute(value, normalization, loudness, overwrite=True)

        request = self.assembly.calls[0]
        self.assertIs(request.normalization_profile, normalization)
        self.assertIs(request.loudness_profile, loudness)
        self.assertTrue(request.overwrite)

    def test_missing_and_directory_sources_fail_before_assembly(self) -> None:
        directory = self.root / "directory"
        directory.mkdir()
        cases = [self.root / "missing.mp4", directory]
        for invalid in cases:
            with self.subTest(invalid=invalid), self.assertRaises(CompositionSourceValidationError) as caught:
                self.service.execute(self._manifest([VideoCompositionScene(scene_id="bad", source_path=invalid, order=0), VideoCompositionScene(scene_id="good", source_path=self.first, order=1)]))
            self.assertIn("composition-01", str(caught.exception))
            self.assertIn(str(invalid), str(caught.exception))
        self.assertEqual(self.assembly.calls, [])

    def test_destination_conflict_fails_before_assembly(self) -> None:
        destination = self.root / "final.mp4"
        destination.write_bytes(b"existing")
        with self.assertRaises(CompositionDestinationConflictError):
            self.service.execute(self._manifest(self._scenes(), destination))
        self.assertEqual(destination.read_bytes(), b"existing")
        self.assertEqual(self.assembly.calls, [])

    def test_assembly_failure_is_wrapped_without_leaking_details(self) -> None:
        self.assembly.error = RuntimeError("Authorization signed-secret raw ffmpeg stderr")
        with self.assertRaises(CompositionAssemblyError) as caught:
            self.service.execute(self._manifest(self._scenes()))
        self.assertIn("composition-01", str(caught.exception))
        self.assertNotIn("secret", str(caught.exception))
        self.assertIsInstance(caught.exception.__cause__, RuntimeError)

    def test_manifest_is_not_mutated_and_duplicate_paths_are_preserved(self) -> None:
        value = self._manifest([
            VideoCompositionScene(scene_id="repeat-two", source_path=self.first, order=5),
            VideoCompositionScene(scene_id="repeat-one", source_path=self.first, order=1),
        ])
        before = value.to_json()
        self.service.execute(value)
        self.assertEqual(value.to_json(), before)
        self.assertEqual(self.assembly.calls[0].sources, (self.first, self.first))

    def _scenes(self):
        return [VideoCompositionScene(scene_id="first", source_path=self.first, order=0), VideoCompositionScene(scene_id="second", source_path=self.second, order=1)]

    def _manifest(self, scenes, destination=None):
        return VideoCompositionManifest(composition_id="composition-01", scenes=tuple(scenes), output=VideoCompositionOutput(destination=destination or self.root / "final.mp4", workspace=self.root / "workspace"))


class FakeAssemblyService:
    def __init__(self) -> None:
        self.calls = []
        self.error = None
        self.expected_existing = None
        info = MediaProbeResult(local_path=Path("final.mp4"), duration_seconds=20, width=1280, height=720, frame_rate=30, video_codec="h264", audio_codec="aac", has_audio=True, container_format="mp4")
        self.artifact = AssembledVideoArtifact(local_path=Path("final.mp4"), byte_size=5, sha256="a" * 64, media_info=info, source_count=2)

    def assemble(self, request):
        self.calls.append(request)
        if self.expected_existing is not None:
            if request.destination.read_bytes() != self.expected_existing:
                raise AssertionError("Executor modified destination before atomic assembly.")
        if self.error:
            raise self.error
        return self.artifact
