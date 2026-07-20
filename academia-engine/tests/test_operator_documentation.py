import contextlib
import io
import unittest
from pathlib import Path
from unittest.mock import patch

from app.cli import episode, episode_attach_local_scene, episode_reconcile_task, episode_recover_scene


ROOT=Path(__file__).resolve().parents[1]


class OperatorDocumentationTests(unittest.TestCase):
    def test_authoritative_guide_documents_every_unified_operation_and_fixture_exists(self):
        guide=(ROOT/"docs"/"OPERATOR_GUIDE.md").read_text(encoding="utf-8")
        for operation in ("--plan","--generate","--status","--resume","--verify","--repair-metadata","--cleanup"):
            self.assertIn(operation,guide)
        self.assertTrue((ROOT/"examples"/"smoke"/"episode-input.json").is_file())
        self.assertIn("docs/OPERATOR_GUIDE.md",(ROOT/"README.md").read_text(encoding="utf-8"))

    def test_documented_unified_argument_combinations_parse(self):
        planning=["--input","examples/smoke/episode-input.json","--production-id","example-001",
                  "--scene-output-dir",".runtime/productions/example-001/scenes",
                  "--workspace",".runtime/media/example-001","--output",".runtime/productions/example-001/final.mp4",
                  "--transition","fade","--transition-duration","0.5"]
        cases=(("--plan",*planning,"--preflight"),("--plan",*planning),("--generate",*planning,"--confirm"),
               ("--status","--production-id","example-001"),("--resume","--production-id","example-001"),
               ("--verify","--production-id","example-001"),("--repair-metadata","--production-id","example-001","--scene-id","scene-0001"),
               ("--cleanup","--older-than-hours","24"),("--cleanup","--older-than-hours","24","--confirm"))
        for arguments in cases:
            with self.subTest(arguments=arguments): self.assertIsNotNone(episode._parser().parse_args(arguments))

    def test_unsafe_combinations_remain_rejected(self):
        cases=(("--resume","--production-id","example-001","--confirm"),
               ("--cleanup","--confirm","--input","episode.json"),
               ("--verify","--production-id","example-001","--workspace","media"))
        for arguments in cases:
            parser=episode._parser(); args=parser.parse_args(arguments)
            with self.subTest(arguments=arguments),self.assertRaises(SystemExit),contextlib.redirect_stderr(io.StringIO()):
                episode._validate_arguments(parser,args)

    def test_unified_help_states_all_operator_safety_boundaries(self):
        help_text=" ".join(episode._parser().format_help().split())
        for phrase in ("provider submission requires --confirm","non-persistent validation",
                       "continue an existing production","dry-run unless --confirm",
                       "minimum disposable-path age"):
            self.assertIn(phrase,help_text)

    def test_specialized_help_describes_provider_and_media_behavior_without_execution(self):
        cases=((episode_reconcile_task,"query and attach","does not submit"),
               (episode_recover_scene,"download one attached succeeded","does not submit"),
               (episode_attach_local_scene,"probe and attach","without provider submission"))
        for module,*phrases in cases:
            output=io.StringIO()
            with self.subTest(module=module.__name__),patch("sys.argv",[module.__name__,"--help"]), \
                 contextlib.redirect_stdout(output),self.assertRaises(SystemExit) as exit_status:
                module.main()
            self.assertEqual(exit_status.exception.code,0)
            normalized=" ".join(output.getvalue().split())
            for phrase in phrases: self.assertIn(phrase,normalized)

    def test_guide_contains_recovery_credit_cleanup_and_media_guards(self):
        guide=(ROOT/"docs"/"OPERATOR_GUIDE.md").read_text(encoding="utf-8").lower()
        for phrase in ("do **not** resubmit","episode_reconcile_task","episode_recover_scene",
                       "episode_attach_local_scene","strictly read-only","dry-run by default",
                       "1280×720","30 FPS","libx264","yuv420p","video-only"):
            self.assertIn(phrase.lower(),guide)
        for forbidden in ("907594518684373074","Authorization: Bearer","signed_url"):
            self.assertNotIn(forbidden.lower(),guide)


if __name__=="__main__": unittest.main()
