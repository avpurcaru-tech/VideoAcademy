from pathlib import Path
from app.services import TaskRegistry
from .contracts import EpisodeProductionStatus, EpisodeSceneStatus, ProductionFailureStage
from .integrity import ProductionIntegrityService
from .registry import ProductionRegistry, utc_now
from .visual_identity import VisualConsistencyRetryPolicy


def is_awaiting_identity_review(scene):
    """The sole durable review-state predicate used by runtime and all CLIs."""
    return (scene.production_status == EpisodeSceneStatus.AWAITING_IDENTITY_REVIEW and
        scene.identity_validation_status == "pending_manual_review" and
        scene.identity_review_status == "pending" and scene.local_path is not None and
        scene.byte_size is not None and scene.sha256 is not None and
        scene.review_requested_at is not None and
        scene.identity_validator_implementation == "manual_review")


class IdentityReviewError(RuntimeError):
    def __init__(self,message,*,scene_id=None,current_status=None):
        super().__init__(message); self.scene_id=scene_id; self.current_status=current_status


class IdentityReviewService:
    def __init__(self, registry=None, integrity=None, retry_policy=None, task_registry=None):
        self.registry=registry or ProductionRegistry()
        self.integrity=integrity or ProductionIntegrityService()
        self.retry_policy=retry_policy or VisualConsistencyRetryPolicy()
        self.tasks=task_registry or TaskRegistry()

    @staticmethod
    def resolve_scene_index(record, scene_id):
        aliases={}
        for index,scene in enumerate(record.scenes):
            aliases[scene.scene_id]=index
            aliases.setdefault(f"scene-{scene.order+1:04d}",index)
            aliases.setdefault(f"shot-{scene.order+1:04d}",index)
        return aliases.get(scene_id)

    def reconcile(self, production_id, scene_id=None):
        """Attach an already-downloaded task artifact and publish review state in one manifest write."""
        record=self.registry.load(production_id); scenes=list(record.scenes); changed=False; now=utc_now()
        indices=range(len(scenes)) if scene_id is None else (self.resolve_scene_index(record,scene_id),)
        if None in indices: raise IdentityReviewError("Scene was not found.",scene_id=scene_id,current_status="unavailable")
        for index in indices:
            scene=scenes[index]
            if is_awaiting_identity_review(scene) or scene.identity_validated is True or not scene.character_reference_images:
                continue
            if scene.provider_task_id is None: continue
            try: task=self.tasks.load(scene.provider_task_id)
            except Exception: continue
            artifact=task.artifact
            if artifact is None: continue
            candidate=scene.model_copy(update={"normalized_status":task.normalized_status,
                "local_path":artifact.local_path,"artifact_id":artifact.artifact_id,"byte_size":artifact.byte_size,
                "sha256":artifact.sha256,"content_type":artifact.content_type})
            if not self.integrity.verify_scene(candidate).valid: continue
            scenes[index]=candidate.model_copy(update={"production_status":EpisodeSceneStatus.AWAITING_IDENTITY_REVIEW,
                "identity_validation_status":"pending_manual_review","identity_review_status":"pending",
                "review_requested_at":now,"identity_validator_implementation":"manual_review",
                "identity_validator_version":"1"})
            changed=True
        if changed:
            record=record.model_copy(update={"scenes":tuple(scenes),"status":EpisodeProductionStatus.FAILED,
                "failure_stage":ProductionFailureStage.VISUAL_IDENTITY_VALIDATION,
                "failure_category":"visual_identity_review_required",
                "safe_message":"Manual visual identity review is required.","updated_at":now})
            self.registry.update(record)
        return record

    def pending(self, production_id):
        return tuple(scene for scene in self.reconcile(production_id).scenes if is_awaiting_identity_review(scene))

    def decide(self, production_id, scene_id, approved, reason=None):
        record=self.reconcile(production_id,scene_id)
        index=self.resolve_scene_index(record,scene_id)
        if index is None: raise IdentityReviewError("Scene was not found.",scene_id=scene_id,current_status="unavailable")
        scene=record.scenes[index]
        if not is_awaiting_identity_review(scene):
            status="downloaded" if scene.local_path else scene.production_status.value
            raise IdentityReviewError("Scene is not awaiting identity review.",scene_id=scene.scene_id,current_status=status)
        if not self.integrity.verify_scene(scene).valid:
            raise IdentityReviewError("Downloaded scene artifact failed integrity verification.")
        now=utc_now()
        if approved:
            scene=scene.model_copy(update={"production_status":EpisodeSceneStatus.READY,
                "identity_validated":True,"identity_review_status":"approved",
                "identity_validation_status":"approved","identity_review_reason":None,"identity_reviewed_at":now})
        else:
            if not reason: raise IdentityReviewError("A safe rejection reason is required.")
            if not self.retry_policy.can_retry(max(0,scene.identity_validation_attempts-1)):
                scene=scene.model_copy(update={"production_status":EpisodeSceneStatus.FAILED,
                    "identity_validated":False,"identity_review_status":"rejected",
                    "identity_validation_status":"rejected",
                    "identity_review_reason":reason,"identity_reviewed_at":now})
                category="visual_identity_retry_exhausted"
            else:
                scene=scene.model_copy(update={"provider_task_id":None,"normalized_status":None,
                    "production_status":EpisodeSceneStatus.PENDING,"identity_validated":False,
                    "identity_review_status":"rejected","identity_review_reason":reason,
                    "identity_validation_status":"rejected",
                    "identity_reviewed_at":now,"rejected_artifact_path":scene.local_path,
                    "rejected_artifact_sha256":scene.sha256,"local_path":None,"artifact_id":None,
                    "byte_size":None,"sha256":None,"content_type":None})
                category="visual_identity_review_rejected"
        scenes=list(record.scenes); scenes[index]=scene
        record=record.model_copy(update={"scenes":tuple(scenes),"status":EpisodeProductionStatus.GENERATING,
            "failed_scene_id":None,"failure_stage":None,"failure_category":None,"safe_message":None,
            "updated_at":now})
        if not approved and category == "visual_identity_retry_exhausted":
            from .contracts import ProductionFailureStage
            record=record.model_copy(update={"status":EpisodeProductionStatus.FAILED,"failed_scene_id":scene_id,
                "failure_stage":ProductionFailureStage.VISUAL_IDENTITY_VALIDATION,
                "failure_category":category,"safe_message":"Visual identity retry limit was reached."})
        self.registry.update(record)
        return record
