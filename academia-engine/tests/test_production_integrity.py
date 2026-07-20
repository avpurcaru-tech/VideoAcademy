import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.cli.episode import main as episode_main
from app.media import MediaProbeResult
from app.models import GenerationTaskStatus
from app.production import (ArtifactIntegrityState, EpisodeProductionOrchestrator, EpisodeProductionStatus,
                            EpisodeSceneStatus, GenerationRequestStore, ProductionArtifactIntegrityError,
                            ProductionFinalArtifactMissingError, ProductionIntegrityService, ProductionRegistry,
                            ProductionSceneArtifactIntegrityError, ProductionArtifactMetadataReconciler,
                            ArtifactMetadataLocalFileError)
from app.services import VideoPollingPolicy
from app.timeline import RenderedTimelineArtifact
from tests.test_episode_production import FakeProbe
from tests.test_episode_reconciliation import NOW, record


class ProductionIntegrityTests(unittest.TestCase):
    def setUp(self): self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name); self.service=ProductionIntegrityService()
    def tearDown(self): self.temp.cleanup()

    def test_all_explicit_artifact_states(self):
        valid=self.root/"valid.mp4"; valid.write_bytes(b"video"); digest=hashlib.sha256(b"video").hexdigest()
        directory=self.root/"directory"; directory.mkdir(); empty=self.root/"empty"; empty.write_bytes(b"")
        cases=((SimpleNamespace(local_path=valid,byte_size=5,sha256=digest),"valid"),
               (SimpleNamespace(local_path=self.root/"missing",byte_size=1,sha256="a"*64),"missing"),
               (SimpleNamespace(local_path=directory,byte_size=1,sha256="a"*64),"not_file"),
               (SimpleNamespace(local_path=empty,byte_size=1,sha256="a"*64),"empty"),
               (SimpleNamespace(local_path=valid,byte_size=4,sha256=digest),"size_mismatch"),
               (SimpleNamespace(local_path=valid,byte_size=5,sha256="a"*64),"hash_mismatch"),
               (SimpleNamespace(local_path=valid,byte_size=None,sha256=digest),"metadata_missing"))
        for metadata,expected in cases:
            with self.subTest(expected=expected): self.assertEqual(self.service.verify_artifact(metadata).state.value,expected)

    def test_valid_succeeded_resume_returns_without_any_provider_or_media_call(self):
        registry=ProductionRegistry(self.root/"productions"); production=record(self.root)
        final=self._final(b"final-video"); registry.create(production.model_copy(update={"status":EpisodeProductionStatus.SUCCEEDED,"final_artifact":final}))
        engine=ExplodingDependency(); renderer=ExplodingDependency(); probe=ExplodingProbe()
        orchestrator=EpisodeProductionOrchestrator(engine,renderer,registry,probe,GenerationRequestStore(self.root/"requests"),clock=lambda:NOW)
        result=orchestrator.resume("episode-001",VideoPollingPolicy(interval_seconds=1,timeout_seconds=2))
        self.assertEqual(result.status,EpisodeProductionStatus.SUCCEEDED); self.assertEqual(engine.calls+renderer.calls+probe.calls,0)

    def test_succeeded_missing_or_corrupt_final_never_renders(self):
        for mode,error_type in (("missing",ProductionFinalArtifactMissingError),("corrupt",ProductionArtifactIntegrityError)):
            with self.subTest(mode=mode):
                registry=ProductionRegistry(self.root/f"productions-{mode}"); production=record(self.root)
                final=self._final(b"content",name=f"{mode}.mp4")
                if mode=="missing": final.local_path.unlink()
                else: final.local_path.write_bytes(b"changed")
                registry.create(production.model_copy(update={"status":EpisodeProductionStatus.SUCCEEDED,"final_artifact":final}))
                dependency=ExplodingDependency(); orchestrator=EpisodeProductionOrchestrator(dependency,dependency,registry,ExplodingProbe(),GenerationRequestStore(self.root/f"requests-{mode}"))
                with self.assertRaises(error_type): orchestrator.resume("episode-001",VideoPollingPolicy(interval_seconds=1,timeout_seconds=2))
                self.assertEqual(dependency.calls,0)

    def test_non_succeeded_corrupt_ready_scene_stops_before_provider_or_renderer(self):
        registry=ProductionRegistry(self.root/"productions-scene"); production=record(self.root)
        path=self.root/"scene.mp4"; path.write_bytes(b"corrupt")
        scene=production.scenes[0].model_copy(update={"production_status":EpisodeSceneStatus.READY,"local_path":path,"byte_size":7,"sha256":"a"*64})
        registry.create(production.model_copy(update={"scenes":(scene,production.scenes[1])}))
        dependency=ExplodingDependency(); orchestrator=EpisodeProductionOrchestrator(dependency,dependency,registry,ExplodingProbe(),GenerationRequestStore(self.root/"requests-scene"))
        with self.assertRaises(ProductionSceneArtifactIntegrityError): orchestrator.resume("episode-001",VideoPollingPolicy(interval_seconds=1,timeout_seconds=2))
        self.assertEqual(dependency.calls,0)

    def test_verify_cli_is_read_only_and_returns_nonzero_for_invalid_artifact(self):
        production=record(self.root); path=self.root/"missing.mp4"
        scene=production.scenes[0].model_copy(update={"production_status":"ready","local_path":path,"byte_size":5,"sha256":"a"*64})
        production=production.model_copy(update={"scenes":(scene,production.scenes[1])})
        with patch("sys.argv",["episode","--verify","--production-id","episode-001"]),patch("app.cli.episode.ProductionRegistry") as registry, \
             patch("app.cli.episode.build_orchestrator") as provider,patch("builtins.print") as emit:
            registry.return_value.load.return_value=production; self.assertEqual(episode_main(),1)
        provider.assert_not_called(); output=" ".join(str(c.args[0]) for c in emit.call_args_list)
        self.assertIn("Artifact integrity: missing",output); self.assertNotIn("prompt",output)

    def test_explicit_metadata_reconciliation_preserves_provider_identity_and_makes_legacy_scene_valid(self):
        registry=ProductionRegistry(self.root/"productions-repair"); production=record(self.root)
        path=self.root/"legacy.mp4"; path.write_bytes(b"legacy-video")
        scene=production.scenes[0].model_copy(update={"provider_task_id":"provider-task","external_correlation_id":"correlation",
            "normalized_status":GenerationTaskStatus.SUCCEEDED,"production_status":EpisodeSceneStatus.READY,"local_path":path,"artifact_id":"provider-artifact",
            "byte_size":None,"sha256":None,"content_type":None})
        registry.create(production.model_copy(update={"scenes":(scene,production.scenes[1])}))
        self.assertEqual(self.service.verify_scene(registry.load("episode-001").scenes[0]).state,ArtifactIntegrityState.METADATA_MISSING)
        repaired=ProductionArtifactMetadataReconciler(registry,clock=lambda:NOW).reconcile_scene("episode-001","scene-0001").scenes[0]
        self.assertEqual(repaired.artifact_id,"provider-artifact"); self.assertEqual(repaired.provider_task_id,"provider-task")
        self.assertEqual(repaired.external_correlation_id,"correlation"); self.assertEqual(repaired.generation_request_reference,scene.generation_request_reference)
        self.assertEqual(repaired.byte_size,len(b"legacy-video")); self.assertEqual(repaired.content_type,"video/mp4")
        self.assertEqual(self.service.verify_scene(repaired).state,ArtifactIntegrityState.VALID)

    def test_metadata_reconciliation_local_id_fallback_and_failure_is_non_mutating(self):
        registry=ProductionRegistry(self.root/"productions-fallback"); production=record(self.root)
        path=self.root/"local.mp4"; path.write_bytes(b"local")
        scene=production.scenes[0].model_copy(update={"production_status":EpisodeSceneStatus.READY,"local_path":path,"artifact_id":None,"byte_size":None,"sha256":None})
        registry.create(production.model_copy(update={"scenes":(scene,production.scenes[1])}))
        repaired=ProductionArtifactMetadataReconciler(registry).reconcile_scene("episode-001","scene-0001").scenes[0]
        self.assertTrue(repaired.artifact_id.startswith("local:"))
        original=registry.load("episode-001"); missing=original.scenes[1].model_copy(update={"production_status":EpisodeSceneStatus.READY,"local_path":self.root/"missing.mp4"})
        registry.update(original.model_copy(update={"scenes":(original.scenes[0],missing)})); before=registry.load("episode-001")
        with self.assertRaises(ArtifactMetadataLocalFileError): ProductionArtifactMetadataReconciler(registry).reconcile_scene("episode-001","scene-0002")
        self.assertEqual(registry.load("episode-001"),before)

    def test_repair_metadata_cli_is_sanitized_and_uses_no_provider(self):
        production=record(self.root); path=self.root/"repair.mp4"; path.write_bytes(b"repair")
        scene=production.scenes[0].model_copy(update={"production_status":EpisodeSceneStatus.READY,"local_path":path,"artifact_id":"artifact","byte_size":6,"sha256":hashlib.sha256(b"repair").hexdigest()})
        production=production.model_copy(update={"scenes":(scene,production.scenes[1])})
        with patch("sys.argv",["episode","--repair-metadata","--production-id","episode-001","--scene-id","scene-0001"]), \
             patch("app.cli.episode.ProductionArtifactMetadataReconciler") as reconciler,patch("app.cli.episode.build_orchestrator") as provider,patch("builtins.print") as emit:
            reconciler.return_value.reconcile_scene.return_value=production; self.assertEqual(episode_main(),0)
        provider.assert_not_called(); output=" ".join(str(c.args[0]) for c in emit.call_args_list)
        self.assertIn("Metadata reconciliation: succeeded",output)
        for forbidden in ("prompt","signed","Authorization","credential"): self.assertNotIn(forbidden,output)

    def _final(self,content,name="final.mp4"):
        path=self.root/name; path.write_bytes(content); digest=hashlib.sha256(content).hexdigest()
        media=MediaProbeResult(local_path=path,duration_seconds=10,width=1280,height=720,frame_rate=30,video_codec="h264",has_audio=False,container_format="mp4")
        return RenderedTimelineArtifact(timeline_id="episode-001",local_path=path,byte_size=len(content),sha256=digest,media_info=media,source_count=2,transition_count=1)


class ExplodingDependency:
    def __init__(self): self.calls=0
    def __getattr__(self,name): self.calls+=1; raise AssertionError(f"unexpected media/provider operation: {name}")
class ExplodingProbe:
    def __init__(self): self.calls=0
    def probe_video(self,*args): self.calls+=1; raise AssertionError("unexpected probe")


if __name__=="__main__": unittest.main()
