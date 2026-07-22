import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from app.production import (ArtifactIntegrityState,EpisodeProductionStatus,EpisodeSceneArtifactMissingError,
    EpisodeSceneStatus,reconcile_succeeded_production)


class RecoveredVideoReconciliationTests(unittest.TestCase):
    def _record(self):
        scenes=tuple(SimpleNamespace(scene_id=f"scene-{index:04d}",production_status=EpisodeSceneStatus.READY,
            local_path=Path(f"scene-{index:04d}.mp4")) for index in range(1,5))
        record=Mock(status=EpisodeProductionStatus.SUCCEEDED,scenes=scenes,final_artifact=Mock(),
            failed_scene_id="scene-0004",failure_stage="video_submission",failure_category="video_request_rejected",
            safe_message="old",submit_http_status=400,submit_provider_code=1201,submit_provider_message="rejected",
            submit_request_id="request",submit_provider_task_id=None,submit_response_shape=("root: object",),
            query_http_status=None,query_provider_code=None,query_provider_task_id=None,query_response_shape=())
        cleared=Mock(status=EpisodeProductionStatus.SUCCEEDED,scenes=scenes,final_artifact=record.final_artifact)
        record.model_copy.return_value=cleared
        return record,cleared

    def test_succeeded_stale_production_is_accepted_and_cleared_atomically(self):
        record,cleared=self._record(); registry=Mock(); registry.load.return_value=record
        valid=SimpleNamespace(state=ArtifactIntegrityState.VALID,valid=True); integrity=Mock()
        integrity.verify_scene.return_value=valid; integrity.verify_artifact.return_value=valid
        result=reconcile_succeeded_production(registry,integrity,"production")
        self.assertIs(cleared,result); registry.update.assert_called_once_with(cleared)
        updates=record.model_copy.call_args.kwargs["update"]
        for name in ("failed_scene_id","failure_stage","failure_category","safe_message","submit_http_status",
            "submit_provider_code","submit_provider_message","submit_request_id","submit_provider_task_id",
            "query_http_status","query_provider_code","query_provider_task_id"):
            self.assertIsNone(updates[name])
        self.assertEqual((),updates["submit_response_shape"]); self.assertEqual((),updates["query_response_shape"])

    def test_missing_ready_scene_file_fails_precisely_without_registry_mutation(self):
        record,_=self._record(); registry=Mock(); registry.load.return_value=record
        missing=SimpleNamespace(state=ArtifactIntegrityState.MISSING,valid=False); integrity=Mock()
        integrity.verify_scene.return_value=missing
        with self.assertRaises(EpisodeSceneArtifactMissingError):
            reconcile_succeeded_production(registry,integrity,"production")
        registry.update.assert_not_called()


if __name__=="__main__": unittest.main()
