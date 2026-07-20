import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.cli.episode import main
from app.production import EpisodeProductionStatus, EpisodeSceneStatus, EpisodeSceneSubmissionError
from tests.test_episode_smoke_cli import result


class UnifiedEpisodeCliTests(unittest.TestCase):
    def test_plan_preflight_and_persistent_plan_delegate_without_provider(self):
        for preflight in (True,False):
            planner=Mock(); planner.preflight.return_value=planned_request(); argv=self.planning_argv("--plan")
            if preflight: argv.append("--preflight")
            with self.subTest(preflight=preflight),patch("sys.argv",argv),patch("app.cli.episode.load_episode",return_value=object()), \
                 patch("app.cli.episode.build_project_planner",return_value=planner),patch("app.cli.episode.build_orchestrator") as provider,patch("builtins.print"):
                self.assertEqual(main(),0)
            provider.assert_not_called()
            if preflight: planner.persist.assert_not_called()
            else: planner.persist.assert_called_once()

    def test_generate_without_confirm_is_non_mutating_and_constructs_no_provider(self):
        planner=Mock(); planner.preflight.return_value=planned_request()
        with patch("sys.argv",self.planning_argv("--generate")),patch("app.cli.episode.ProductionRegistry") as registry, \
             patch("app.cli.episode.load_episode",return_value=object()),patch("app.cli.episode.build_project_planner",return_value=planner), \
             patch("app.cli.episode.build_orchestrator") as provider,patch("builtins.print"):
            registry.return_value.exists.return_value=False; self.assertEqual(main(),2)
        planner.persist.assert_not_called(); provider.assert_not_called()

    def test_confirmed_generate_persists_then_delegates_to_existing_orchestrator(self):
        planner=Mock(); request=planned_request(); planner.preflight.return_value=request
        orchestrator=Mock(); orchestrator.produce.return_value=result(Path("final.mp4"))
        with patch("sys.argv",self.planning_argv("--generate")+["--confirm"]),patch("app.cli.episode.ProductionRegistry") as registry, \
             patch("app.cli.episode.load_episode",return_value=object()),patch("app.cli.episode.build_project_planner",return_value=planner), \
             patch("app.cli.episode.build_orchestrator",return_value=orchestrator),patch("builtins.print"):
            registry.return_value.exists.return_value=False; self.assertEqual(main(),0)
        planner.persist.assert_called_once_with(request); orchestrator.produce.assert_called_once()

    def test_existing_production_blocks_generation_before_provider(self):
        with patch("sys.argv",self.planning_argv("--generate")+["--confirm"]),patch("app.cli.episode.ProductionRegistry") as registry, \
             patch("app.cli.episode.build_orchestrator") as provider,patch("builtins.print") as emit:
            registry.return_value.exists.return_value=True; self.assertEqual(main(),1)
        provider.assert_not_called(); self.assertIn("Use --status", " ".join(str(c.args[0]) for c in emit.call_args_list))

    def test_status_uses_read_only_summary_and_no_provider(self):
        summary=SimpleNamespace(production_id="episode-001",status=EpisodeProductionStatus.FAILED,
            scenes=(SimpleNamespace(scene_id="scene-0001",production_status=EpisodeSceneStatus.READY,provider_status=None,
                provider_task_id=None,local_artifact=Path("scene.mp4")),),final_artifact_present=False,final_path=None)
        with patch("sys.argv",["episode","--status","--production-id","episode-001"]), \
             patch("app.cli.episode.EpisodeProductionSummaryService") as service,patch("app.cli.episode.build_orchestrator") as provider,patch("builtins.print") as emit:
            service.return_value.load.return_value=summary; self.assertEqual(main(),0)
        provider.assert_not_called(); output=" ".join(str(c.args[0]) for c in emit.call_args_list)
        self.assertIn("Production scene status: ready",output); self.assertIn("Provider status: -",output)

    def test_resume_requires_only_id_and_delegates(self):
        orchestrator=Mock(); orchestrator.resume.return_value=result(Path("final.mp4"))
        with patch("sys.argv",["episode","--resume","--production-id","episode-001"]),patch("app.cli.episode.build_orchestrator",return_value=orchestrator),patch("builtins.print"):
            self.assertEqual(main(),0)
        orchestrator.resume.assert_called_once()

    def test_invalid_combinations_are_rejected_before_services(self):
        cases=(["episode","--status","--production-id","x","--preflight"],
               ["episode","--resume","--production-id","x","--confirm"],
               ["episode","--plan","--production-id","x"])
        for argv in cases:
            with self.subTest(argv=argv),patch("sys.argv",argv),patch("app.cli.episode.build_orchestrator") as builder,self.assertRaises(SystemExit): main()
            builder.assert_not_called()

    def test_safe_error_suppresses_raw_secrets_and_gives_durable_guidance(self):
        orchestrator=Mock(); orchestrator.resume.side_effect=EpisodeSceneSubmissionError("prompt signed URL Authorization credential")
        with patch("sys.argv",["episode","--resume","--production-id","episode-001"]),patch("app.cli.episode.build_orchestrator",return_value=orchestrator), \
             patch("app.cli.episode.ProductionRegistry") as registry,patch("builtins.print") as emit:
            registry.return_value.exists.return_value=True; self.assertEqual(main(),1)
        output=" ".join(str(c.args[0]) for c in emit.call_args_list); self.assertIn("Durable production state exists",output)
        for forbidden in ("prompt","signed URL","Authorization","credential"): self.assertNotIn(forbidden,output)

    @staticmethod
    def planning_argv(operation):
        return ["episode",operation,"--input","episode.json","--production-id","episode-001","--scene-output-dir","scenes",
                "--workspace","media","--output","final.mp4","--transition","fade","--transition-duration","0.5"]


def planned_request():
    return SimpleNamespace(production_id="episode-001",generation_request_references=(SimpleNamespace(reference_id="episode-001-scene-0001"),SimpleNamespace(reference_id="episode-001-scene-0002")),
                           final_output_path=Path("final.mp4"),media_workspace=Path("media"))


if __name__=="__main__": unittest.main()
