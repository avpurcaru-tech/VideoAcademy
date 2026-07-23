import os
import unittest
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.production import (EpisodeProductionStatus, EpisodeSceneResult, EpisodeSceneStatus,
    IdentityReviewService, ManualReviewVisualIdentityValidator, ProductionRecord,
    VisualIdentityValidationMode, VisualIdentityValidatorFactory, is_awaiting_identity_review)


class _Registry:
    def __init__(self, record): self.record=record; self.updates=[]
    def load(self, production_id): return self.record
    def update(self, record): self.record=record; self.updates.append(record)

class _Integrity:
    def verify_scene(self, scene): return SimpleNamespace(valid=True)

class _Tasks:
    def __init__(self): self.loads=0
    def load(self, task_id):
        self.loads+=1
        artifact=SimpleNamespace(local_path=Path("downloaded.mp4"),artifact_id="artifact",byte_size=20,
            sha256="b"*64,content_type="video/mp4")
        return SimpleNamespace(artifact=artifact,normalized_status="succeeded")


class VisualIdentityRuntimeWiringTests(unittest.TestCase):
    def test_default_is_required_and_manual_validator_never_claims_automatic_pass(self):
        with patch.dict(os.environ,{},clear=True):
            runtime=VisualIdentityValidatorFactory().construct_runtime()
        self.assertEqual(runtime.mode,VisualIdentityValidationMode.REQUIRED)
        self.assertFalse(runtime.automatic_available)
        result=runtime.validator.validate(Path("downloaded.mp4"),())
        self.assertFalse(result.valid); self.assertTrue(result.review_required); self.assertFalse(result.automatic)

    def test_advisory_and_explicit_disabled_modes_are_authoritative(self):
        advisory=VisualIdentityValidatorFactory().construct_runtime("advisory")
        disabled=VisualIdentityValidatorFactory().construct_runtime("disabled")
        self.assertIsInstance(advisory.validator,ManualReviewVisualIdentityValidator)
        self.assertIsNone(disabled.validator)

    def _record(self):
        scene=EpisodeSceneResult.model_construct(scene_id="shot-0001",order=0,
            generation_request_reference=None,production_status=EpisodeSceneStatus.AWAITING_IDENTITY_REVIEW,
            local_path=Path("scene.mp4"),sha256="a"*64,byte_size=10,identity_validation_attempts=1,
            identity_validation_status="pending_manual_review",identity_review_status="pending",
            review_requested_at=datetime.now(timezone.utc),identity_validator_implementation="manual_review",
            character_reference_images=())
        return ProductionRecord.model_construct(production_id="production",status=EpisodeProductionStatus.FAILED,
            provider="kling_image_to_video",scenes=(scene,),identity_validation_mode="required")

    def test_approval_marks_only_reviewed_artifact_ready(self):
        registry=_Registry(self._record())
        result=IdentityReviewService(registry,_Integrity()).decide("production","shot-0001",True)
        self.assertEqual(result.scenes[0].production_status,EpisodeSceneStatus.READY)
        self.assertTrue(result.scenes[0].identity_validated)
        self.assertEqual(result.scenes[0].identity_review_status,"approved")

    def test_rejection_retains_audit_and_clears_only_scene_task_for_retry(self):
        original=self._record(); scene=original.scenes[0].model_copy(update={"provider_task_id":"task-1"})
        registry=_Registry(original.model_copy(update={"scenes":(scene,)}))
        result=IdentityReviewService(registry,_Integrity()).decide("production","shot-0001",False,"identity_mismatch")
        rejected=result.scenes[0]
        self.assertIsNone(rejected.provider_task_id); self.assertEqual(rejected.production_status,EpisodeSceneStatus.PENDING)
        self.assertEqual(rejected.identity_review_reason,"identity_mismatch")
        self.assertEqual(rejected.rejected_artifact_path,Path("scene.mp4"))

    def test_downloaded_task_reconciles_atomically_without_submit_or_download(self):
        record=self._record(); scene=record.scenes[0].model_copy(update={"scene_id":"scene-0001",
            "production_status":EpisodeSceneStatus.GENERATING,"provider_task_id":"task-1","local_path":None,
            "byte_size":None,"sha256":None,"identity_validation_status":None,"identity_review_status":None,
            "review_requested_at":None,"identity_validator_implementation":None,
            "character_reference_images":(SimpleNamespace(character_id="luca"),)})
        registry=_Registry(record.model_copy(update={"scenes":(scene,)})); tasks=_Tasks()
        repaired=IdentityReviewService(registry,_Integrity(),task_registry=tasks).reconcile("production","shot-0001")
        self.assertEqual(tasks.loads,1); self.assertEqual(len(registry.updates),1)
        self.assertTrue(is_awaiting_identity_review(repaired.scenes[0]))
        self.assertEqual(repaired.scenes[0].scene_id,"scene-0001")

    def test_alias_approval_uses_canonical_manifest_id_after_reconciliation(self):
        record=self._record(); scene=record.scenes[0].model_copy(update={"scene_id":"scene-0001",
            "production_status":EpisodeSceneStatus.GENERATING,"provider_task_id":"task-1","local_path":None,
            "byte_size":None,"sha256":None,"identity_validation_status":None,"identity_review_status":None,
            "review_requested_at":None,"identity_validator_implementation":None,
            "character_reference_images":(SimpleNamespace(character_id="luca"),)})
        registry=_Registry(record.model_copy(update={"scenes":(scene,)})); tasks=_Tasks()
        approved=IdentityReviewService(registry,_Integrity(),task_registry=tasks).decide("production","shot-0001",True)
        self.assertEqual(approved.scenes[0].scene_id,"scene-0001")
        self.assertEqual(approved.scenes[0].identity_validation_status,"approved")


if __name__ == "__main__": unittest.main()
