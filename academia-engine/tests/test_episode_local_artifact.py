import hashlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from app.cli.episode_attach_local_scene import main as attach_main
from app.media import MediaProbeResult
from app.production import (
    EpisodeLocalArtifactConflictError,
    EpisodeLocalArtifactMediaError,
    EpisodeLocalArtifactService,
    EpisodeLocalArtifactSourceError,
    EpisodeProductionOrchestrator,
    EpisodeProductionStatus,
    GenerationRequestStore,
    ProductionRegistry,
)
from app.services import VideoPollingPolicy
from tests.test_episode_production import FakeProbe, FakeRenderer
from tests.test_episode_reconciliation import NOW, record


class EpisodeLocalArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        self.registry=ProductionRegistry(self.root/"productions"); self.registry.create(record(self.root))
        self.probe=LocalProbe(); self.service=EpisodeLocalArtifactService(self.registry, self.probe, clock=lambda: NOW)

    def tearDown(self): self.temp.cleanup()

    def test_successful_copy_has_deterministic_identity_metadata_and_preserves_semantic_identity(self):
        source=self.root/"external.mp4"; content=b"valid-video-bytes"; source.write_bytes(content)
        before=self.registry.load("episode-001").scenes[1].generation_request_reference
        updated=self.service.attach_local_artifact("episode-001", "scene-0002", source); scene=updated.scenes[1]
        digest=hashlib.sha256(content).hexdigest(); destination=self.root/"scenes/scene-0002.mp4"
        self.assertEqual(destination.read_bytes(), content); self.assertEqual(scene.local_path, destination)
        self.assertEqual(scene.artifact_id, f"local:{digest}"); self.assertEqual(scene.byte_size, len(content))
        self.assertEqual(scene.sha256, digest); self.assertEqual(scene.content_type, "video/mp4")
        self.assertEqual(scene.generation_request_reference, before); self.assertIsNone(scene.provider_task_id)
        self.assertIsNone(scene.external_correlation_id); self.assertIsNone(scene.normalized_status)
        self.assertEqual(updated.status, EpisodeProductionStatus.PENDING)
        self.assertFalse((self.root/"scenes/scene-0002.mp4.part").exists())

    def test_source_equal_to_destination_validates_without_copy(self):
        destination=self.root/"scenes/scene-0002.mp4"; destination.parent.mkdir(); destination.write_bytes(b"same")
        updated=self.service.attach_local_artifact("episode-001", "scene-0002", destination)
        self.assertEqual(updated.scenes[1].local_path, destination); self.assertEqual(self.probe.paths, [destination])

    def test_missing_directory_empty_invalid_and_destination_conflict_leave_registry_unchanged(self):
        original=self.registry.load("episode-001")
        directory=self.root/"directory"; directory.mkdir(); empty=self.root/"empty.mp4"; empty.write_bytes(b"")
        invalid=self.root/"invalid.mp4"; invalid.write_bytes(b"bad")
        destination=self.root/"scenes/scene-0002.mp4"; destination.parent.mkdir(); destination.write_bytes(b"existing")
        cases=((self.root/"missing.mp4", EpisodeLocalArtifactSourceError), (directory, EpisodeLocalArtifactSourceError),
               (empty, EpisodeLocalArtifactConflictError), (invalid, EpisodeLocalArtifactConflictError))
        for source, error_type in cases:
            with self.subTest(source=source), self.assertRaises(error_type):
                self.service.attach_local_artifact("episode-001", "scene-0002", source)
            self.assertEqual(self.registry.load("episode-001"), original)

    def test_invalid_media_cleans_temporary_and_preserves_registry(self):
        source=self.root/"invalid.mp4"; source.write_bytes(b"invalid"); self.probe.fail=True
        original=self.registry.load("episode-001")
        with self.assertRaises(EpisodeLocalArtifactMediaError): self.service.attach_local_artifact("episode-001", "scene-0002", source)
        self.assertEqual(self.registry.load("episode-001"), original)
        self.assertFalse((self.root/"scenes/scene-0002.mp4.part").exists()); self.assertFalse((self.root/"scenes/scene-0002.mp4").exists())

    def test_provider_backed_scene_rejects_manual_injection(self):
        original=self.registry.load("episode-001"); scene=original.scenes[1].model_copy(update={"provider_task_id":"task"})
        self.registry.update(original.model_copy(update={"scenes":(original.scenes[0],scene)})); source=self.root/"x.mp4"; source.write_bytes(b"video")
        with self.assertRaises(EpisodeLocalArtifactConflictError): self.service.attach_local_artifact("episode-001","scene-0002",source)

    def test_all_local_resume_uses_zero_provider_operations_and_renders(self):
        for index in (1,2):
            source=self.root/f"source-{index}.mp4"; source.write_bytes(f"video-{index}".encode())
            self.service.attach_local_artifact("episode-001",f"scene-{index:04d}",source)
        engine=NoProviderEngine(); renderer=FakeRenderer(self.root/"final.mp4")
        orchestrator=EpisodeProductionOrchestrator(engine,renderer,self.registry,FakeProbe(),GenerationRequestStore(self.root/"requests"),clock=lambda:NOW)
        result=orchestrator.resume("episode-001",VideoPollingPolicy(interval_seconds=1,timeout_seconds=10))
        self.assertEqual(result.status,EpisodeProductionStatus.SUCCEEDED); self.assertIsNotNone(renderer.plan)
        self.assertEqual(engine.calls,0)

    def test_cli_output_is_sanitized(self):
        source=self.root/"source.mp4"; source.write_bytes(b"video"); updated=self.service.attach_local_artifact("episode-001","scene-0002",source)
        service=Mock(); service.attach_local_artifact.return_value=updated
        argv=["episode_attach_local_scene","--production-id","episode-001","--scene-id","scene-0002","--input",str(source)]
        with patch("sys.argv",argv),patch("app.cli.episode_attach_local_scene.build_service",return_value=service),patch("builtins.print") as emit:
            self.assertEqual(attach_main(),0)
        output=" ".join(str(call.args[0]) for call in emit.call_args_list); self.assertIn("Attachment: succeeded",output)
        for forbidden in ("prompt","Authorization","signed","credential","ffprobe"): self.assertNotIn(forbidden,output)


class LocalProbe:
    def __init__(self): self.paths=[]; self.fail=False
    def probe_video(self,path):
        self.paths.append(path)
        if self.fail: raise RuntimeError("raw ffprobe prompt signed URL")
        return MediaProbeResult(local_path=path,duration_seconds=5,width=1280,height=720,frame_rate=30,video_codec="h264",has_audio=False,container_format="mp4")

class NoProviderEngine:
    def __init__(self): self.calls=0
    def __getattr__(self,name):
        self.calls+=1; raise AssertionError(f"provider operation {name} must not be called")


if __name__ == "__main__": unittest.main()
