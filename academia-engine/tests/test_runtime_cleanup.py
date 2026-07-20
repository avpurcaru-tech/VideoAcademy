import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.cli.episode import main as episode_main
from app.production import (CleanupCandidateSafetyError, CleanupCategory, CleanupConfirmationError,
                            CleanupEntry, CleanupPlan, EpisodeProductionStatus, ProductionRegistry,
                            RuntimeCleanupService)
from tests.test_episode_reconciliation import record


class RuntimeCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(); self.root=Path(self.temporary.name)/".runtime"
        self.service=RuntimeCleanupService()

    def tearDown(self): self.temporary.cleanup()

    def _stale(self,path: Path,content=b"temporary") -> Path:
        path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(content)
        old=time.time()-7200; os.utime(path,(old,old)); return path

    def test_dry_run_detects_stale_part_and_deletes_nothing(self):
        candidate=self._stale(self.root/"requests"/"request.json.part")
        plan=self.service.scan(self.root,3600)
        self.assertEqual(plan.candidate_count,1); self.assertEqual(plan.recoverable_bytes,len(b"temporary"))
        self.assertTrue(candidate.exists())

    def test_recent_part_is_protected_by_age_threshold(self):
        path=self.root/"kling"/"tasks"/"task.json.part"; path.parent.mkdir(parents=True); path.write_bytes(b"recent")
        self.assertEqual(self.service.scan(self.root,3600).candidate_count,0)

    def test_allowlisted_workspaces_only(self):
        assembly=self.root/"media"/"episode-001"/"assembly-abandoned"; self._stale(assembly/"render.bin")
        smoke=self.root/"media"/"smoke-episode-001"; self._stale(smoke/"timeline.bin")
        arbitrary=self.root/"media"/"important"; self._stale(arbitrary/"keep.bin")
        old=time.time()-7200
        for directory in (assembly,smoke,arbitrary): os.utime(directory,(old,old))
        plan=self.service.scan(self.root,3600)
        self.assertEqual({entry.category for entry in plan.entries},
                         {CleanupCategory.ASSEMBLY_WORKSPACE,CleanupCategory.SMOKE_MEDIA_WORKSPACE})
        result=self.service.execute(plan)
        self.assertEqual((result.deleted_count,result.failed_count),(2,0)); self.assertTrue(arbitrary.exists())

    def test_confirmed_cleanup_requires_threshold(self):
        self._stale(self.root/"requests"/"one.json.part")
        with self.assertRaises(CleanupConfirmationError): self.service.execute(self.service.scan(self.root))

    def test_confirmed_cleanup_counts_recovered_bytes_and_empty_plan(self):
        first=self._stale(self.root/"requests"/"one.json.part",b"12")
        second=self._stale(self.root/"productions"/"two.json.part",b"345")
        result=self.service.execute(self.service.scan(self.root,1))
        self.assertEqual((result.candidate_count,result.deleted_count,result.recovered_bytes),(2,2,5))
        self.assertFalse(first.exists()); self.assertFalse(second.exists())
        empty=self.service.execute(self.service.scan(self.root,1)); self.assertEqual(empty.candidate_count,0)

    def test_durable_layout_and_records_are_never_candidates(self):
        productions=self.root/"productions"; registry=ProductionRegistry(productions)
        production=record(self.root).model_copy(update={"status":EpisodeProductionStatus.SUCCEEDED})
        scene_path=productions/"smoke-episode-001"/"scenes"/"scene-0001.mp4"
        final_path=productions/"smoke-episode-001"/"final.mp4"
        scene_path.parent.mkdir(parents=True); scene_path.write_bytes(b"scene"); final_path.write_bytes(b"final")
        scene=production.scenes[0].model_copy(update={"local_path":scene_path})
        registry.create(production.model_copy(update={"production_id":"smoke-episode-001","scenes":(scene,production.scenes[1]),
            "final_output_path":final_path,"scene_output_directory":scene_path.parent}))
        request=self._stale(self.root/"requests"/"durable.json",b"request")
        task=self._stale(self.root/"kling"/"tasks"/"durable.json",b"task")
        plan=self.service.scan(self.root)
        self.assertEqual(plan.candidate_count,0)
        for path in (scene_path,final_path,productions/"smoke-episode-001.json",request,task): self.assertTrue(path.exists())

    def test_workspace_containing_referenced_scene_is_protected(self):
        workspace=self.root/"media"/"smoke-protected"; scene=self._stale(workspace/"scene.mp4")
        production=record(self.root); updated=production.scenes[0].model_copy(update={"local_path":scene})
        ProductionRegistry(self.root/"productions").create(production.model_copy(update={"scenes":(updated,production.scenes[1])}))
        self.assertEqual(self.service.scan(self.root,1).candidate_count,0); self.assertTrue(workspace.exists())

    def test_symlink_escape_and_forged_traversal_are_rejected(self):
        outside=Path(self.temporary.name)/"outside"; outside.mkdir(); link=self.root/"media"/"smoke-link"
        link.parent.mkdir(parents=True)
        try: link.symlink_to(outside,target_is_directory=True)
        except OSError: self.skipTest("directory symlinks are unavailable")
        with self.assertRaises(CleanupCandidateSafetyError): self.service.scan(self.root,1)
        external=self._stale(Path(self.temporary.name)/"outside.part")
        forged=CleanupPlan(self.root.resolve(),(CleanupEntry(external.resolve(),CleanupCategory.ATOMIC_PART_FILE,"forged",9999,9),),1,1)
        result=self.service.execute(forged)
        self.assertEqual(result.failed_count,1); self.assertTrue(external.exists())

    def test_deletion_failure_is_isolated(self):
        first=self._stale(self.root/"requests"/"one.json.part",b"one")
        second=self._stale(self.root/"requests"/"two.json.part",b"two")
        plan=self.service.scan(self.root,1)
        real_unlink=Path.unlink
        def unlink(path,*args,**kwargs):
            if path==first.resolve(): raise OSError("secret failure")
            return real_unlink(path,*args,**kwargs)
        with patch.object(Path,"unlink",unlink): result=self.service.execute(plan)
        self.assertEqual((result.deleted_count,result.failed_count),(1,1)); self.assertTrue(first.exists()); self.assertFalse(second.exists())

    def test_cli_is_dry_run_by_default_and_output_is_sanitized(self):
        entry=CleanupEntry(Path("safe.part"),CleanupCategory.ATOMIC_PART_FILE,"stale atomic-writer temporary file",10,4)
        plan=CleanupPlan(Path(".runtime"),(entry,),1,3600); service=unittest.mock.Mock(); service.scan.return_value=plan
        argv=["episode","--cleanup","--older-than-hours","1"]
        with patch("sys.argv",argv),patch("app.cli.episode.RuntimeCleanupService",return_value=service),patch("builtins.print") as emit:
            self.assertEqual(episode_main(),0)
        service.execute.assert_not_called(); output=" ".join(str(call.args[0]) for call in emit.call_args_list)
        self.assertIn("Mode: dry-run",output); self.assertIn("Candidates: 1",output)
        for forbidden in ("file contents","Authorization","credential","prompt","signed URL"): self.assertNotIn(forbidden,output)


if __name__=="__main__": unittest.main()
