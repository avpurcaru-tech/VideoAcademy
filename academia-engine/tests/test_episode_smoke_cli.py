import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.cli.episode_smoke_test import main
from app.media import MediaProbeResult
from app.production import (EpisodeProductionError, EpisodeProductionResult, EpisodeProductionStatus, EpisodeSceneResult,
                            GenerationRequestConflictError, GenerationRequestCorruptedError, GenerationRequestNotFoundError)
from app.timeline import RenderedTimelineArtifact
from tests.test_episode_production import generation


class EpisodeSmokeCliTests(unittest.TestCase):
    def test_without_confirm_warns_before_constructing_any_real_component(self):
        argv = ["episode_smoke_test", "--production-id", "smoke-001"]
        with patch("sys.argv", argv), patch("app.cli.episode_smoke_test.build_orchestrator") as builder, patch("builtins.print") as emit:
            self.assertEqual(main(), 2)
        builder.assert_not_called()
        text = " ".join(str(call.args[0]) for call in emit.call_args_list)
        self.assertIn("may consume credits", text)

    def test_new_production_stores_deterministic_references_before_produce(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); first = root / "one.json"; second = root / "two.json"
            first.write_text(generation("request-1", 1).model_dump_json(), encoding="utf-8")
            second.write_text(generation("request-2", 2).model_dump_json(), encoding="utf-8")
            argv = self._produce_argv(first, second)
            state = {"stored": 0}; store = Mock()
            store.resolve.side_effect = GenerationRequestNotFoundError("missing")
            store.create.side_effect = lambda *_: state.__setitem__("stored", state["stored"] + 1)
            orchestrator = Mock()
            def produce(request, policy):
                self.assertEqual(state["stored"], 2)
                self.assertEqual([str(value) for value in request.generation_request_references],
                                 ["smoke-episode-001-scene-0001", "smoke-episode-001-scene-0002"])
                return result(root / "final.mp4")
            orchestrator.produce.side_effect = produce
            with patch("sys.argv", argv), patch("app.cli.episode_smoke_test.ProductionRegistry") as registry, \
                 patch("app.cli.episode_smoke_test.GenerationRequestStore", return_value=store), \
                 patch("app.cli.episode_smoke_test.build_orchestrator", return_value=orchestrator), patch("builtins.print") as emit:
                registry.return_value.exists.return_value = False
                self.assertEqual(main(), 0)
            orchestrator.resume.assert_not_called()
            output = "\n".join(str(call.args[0]) for call in emit.call_args_list)
            self.assertIn("Real provider generation may consume credits.", output)
            self.assertIn("Video codec: h264", output)
            for forbidden in ("secret semantic prompt", "signed", "Authorization", "credential"):
                self.assertNotIn(forbidden, output)

    def test_existing_production_never_calls_produce(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); one = root / "one.json"; two = root / "two.json"
            one.write_text(generation("request-1", 1).model_dump_json()); two.write_text(generation("request-2", 2).model_dump_json())
            orchestrator = Mock()
            with patch("sys.argv", self._produce_argv(one, two)), patch("app.cli.episode_smoke_test.ProductionRegistry") as registry, \
                 patch("app.cli.episode_smoke_test.build_orchestrator", return_value=orchestrator), patch("builtins.print") as emit:
                registry.return_value.exists.return_value = True
                self.assertEqual(main(), 1)
            orchestrator.produce.assert_not_called(); orchestrator.resume.assert_not_called()
            self.assertIn("Use --resume to continue it", " ".join(str(c.args[0]) for c in emit.call_args_list))

    def test_resume_needs_only_id_and_never_calls_produce_or_request_store(self):
        orchestrator = Mock(); orchestrator.resume.return_value = result(Path("final.mp4"))
        argv = ["episode_smoke_test", "--production-id", "smoke-episode-001", "--resume", "--interval", "2", "--timeout", "900"]
        with patch("sys.argv", argv), patch("app.cli.episode_smoke_test.build_orchestrator", return_value=orchestrator), \
             patch("app.cli.episode_smoke_test.GenerationRequestStore") as store, patch("builtins.print"):
            self.assertEqual(main(), 0)
        orchestrator.resume.assert_called_once(); orchestrator.produce.assert_not_called(); store.assert_not_called()

    def test_failure_output_does_not_echo_exception_secrets(self):
        orchestrator = Mock(); orchestrator.resume.side_effect = EpisodeProductionError("signed URL Authorization api_key prompt")
        argv = ["episode_smoke_test", "--production-id", "smoke-episode-001", "--resume"]
        with patch("sys.argv", argv), patch("app.cli.episode_smoke_test.build_orchestrator", return_value=orchestrator), patch("builtins.print") as emit:
            self.assertEqual(main(), 1)
        output = " ".join(str(call.args[0]) for call in emit.call_args_list)
        for forbidden in ("signed URL", "Authorization", "api_key", "prompt"):
            self.assertNotIn(forbidden, output)

    def test_preflight_reports_missing_malformed_and_schema_errors_safely(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); good = root / "good.json"; good.write_text(generation("request-1", 1).model_dump_json())
            cases = ((root / "missing.json", "Request file not found"),)
            malformed = root / "malformed.json"; malformed.write_text('{"secret":"Authorization prompt"')
            invalid = root / "invalid.json"; invalid.write_text('{"request_id":123,"video_request":{"secret":"signed URL"}}')
            cases += ((malformed, "Request JSON is invalid"), (invalid, "Generation request validation failed"))
            for first, expected in cases:
                with self.subTest(expected=expected), patch("sys.argv", self._preflight_argv(first, good)), \
                     patch("app.cli.episode_smoke_test.ProductionRegistry") as registry, \
                     patch("app.cli.episode_smoke_test.build_orchestrator") as builder, patch("builtins.print") as emit:
                    registry.return_value.exists.return_value = False
                    self.assertEqual(main(), 1); builder.assert_not_called()
                    output = " ".join(str(c.args[0]) for c in emit.call_args_list)
                    self.assertIn(expected, output)
                    for forbidden in ("Authorization prompt", "signed URL", '"secret"'):
                        self.assertNotIn(forbidden, output)

    def test_successful_preflight_never_builds_provider_or_produces(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); one=root/"one.json"; two=root/"two.json"
            one.write_text(generation("request-1", 1).model_dump_json()); two.write_text(generation("request-2", 2).model_dump_json())
            store=Mock(); store.resolve.side_effect=GenerationRequestNotFoundError("missing")
            with patch("sys.argv", self._preflight_argv(one, two)), patch("app.cli.episode_smoke_test.ProductionRegistry") as registry, \
                 patch("app.cli.episode_smoke_test.GenerationRequestStore", return_value=store), \
                 patch("app.cli.episode_smoke_test.build_orchestrator") as builder, patch("builtins.print") as emit:
                registry.return_value.exists.return_value=False
                self.assertEqual(main(), 0)
            builder.assert_not_called(); store.create.assert_not_called()
            output=" ".join(str(c.args[0]) for c in emit.call_args_list)
            self.assertIn("Preflight passed", output); self.assertIn("smoke-episode-001-scene-0001", output)

    def test_preflight_reports_store_conflict_and_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); one=root/"one.json"; two=root/"two.json"
            one.write_text(generation("request-1", 1).model_dump_json()); two.write_text(generation("request-2", 2).model_dump_json())
            for side_effect, expected in ((None, "Request reference conflict"), (GenerationRequestCorruptedError("raw secret"), "record is corrupted")):
                store=Mock()
                if side_effect is None: store.resolve.return_value=generation("different", 2)
                else: store.resolve.side_effect=side_effect
                with self.subTest(expected=expected), patch("sys.argv", self._preflight_argv(one,two)), \
                     patch("app.cli.episode_smoke_test.ProductionRegistry") as registry, \
                     patch("app.cli.episode_smoke_test.GenerationRequestStore", return_value=store), \
                     patch("app.cli.episode_smoke_test.build_orchestrator") as builder, patch("builtins.print") as emit:
                    registry.return_value.exists.return_value=False; self.assertEqual(main(),1); builder.assert_not_called()
                    self.assertIn(expected, " ".join(str(c.args[0]) for c in emit.call_args_list))

    @staticmethod
    def _produce_argv(first, second):
        return ["episode_smoke_test", "--production-id", "smoke-episode-001", "--request", str(first), "--request", str(second),
                "--provider", "kling", "--scene-output-dir", "scenes", "--workspace", "media", "--output", "final.mp4",
                "--transition", "fade", "--transition-duration", "0.5", "--confirm"]

    @staticmethod
    def _preflight_argv(first, second):
        values = EpisodeSmokeCliTests._produce_argv(first, second)
        values[-1] = "--preflight"
        return values


def result(path):
    media = MediaProbeResult(local_path=path, duration_seconds=9.5, width=1280, height=720, frame_rate=30,
                            video_codec="h264", audio_codec=None, has_audio=False, container_format="mp4")
    artifact = RenderedTimelineArtifact(timeline_id="smoke-episode-001", local_path=path, byte_size=10, sha256="f" * 64,
                                        media_info=media, source_count=2, transition_count=1)
    scenes = tuple(EpisodeSceneResult(scene_id=f"scene-{index:04d}", order=index - 1,
        generation_request_reference={"reference_id":f"ref-{index}"}, provider_task_id=f"task-{index}",
        normalized_status="succeeded", local_path=Path(f"scene-{index}.mp4"), artifact_id=f"a-{index}", sha256="a"*64)
        for index in (1, 2))
    return EpisodeProductionResult(production_id="smoke-episode-001", status=EpisodeProductionStatus.SUCCEEDED,
                                   scenes=scenes, final_artifact=artifact)


if __name__ == "__main__": unittest.main()
