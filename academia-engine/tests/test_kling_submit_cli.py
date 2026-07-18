import unittest
from unittest.mock import patch

from app.cli.kling_submit_test import main
from app.models import GenerationTask, GenerationTaskStatus
from app.providers import KlingHttpError


class KlingSubmitCliTests(unittest.TestCase):
    def test_cli_requires_explicit_confirmation(self) -> None:
        with patch("sys.argv", ["kling_submit_test"]):
            self.assertEqual(main(), 2)

    def test_cli_submits_only_after_confirmation(self) -> None:
        task = GenerationTask(
            request_id="kling-smoke-test",
            external_task_id="task-01",
            provider_name="kling",
            provider_status="submitted",
            normalized_status=GenerationTaskStatus.SUBMITTED,
        )
        task.external_correlation_id = "external-correlation-01"
        task.provider_request_id = "provider-request-01"
        with patch("sys.argv", ["kling_submit_test", "--confirm"]), patch(
            "app.cli.kling_submit_test.KlingProvider"
        ) as provider_class, patch("app.cli.kling_submit_test.sync_task_record") as registry_sync, patch(
            "app.cli.kling_submit_test.print"
        ) as print_mock:
            provider_class.return_value.submit_scene.return_value = task

            self.assertEqual(main(), 0)

        provider_class.return_value.submit_scene.assert_called_once()
        registry_sync.assert_called_once_with(task)
        output = "\n".join(call.args[0] for call in print_mock.call_args_list)
        self.assertIn("Kling task id: task-01", output)
        self.assertIn("External correlation id: external-correlation-01", output)
        self.assertIn("Provider request id: provider-request-01", output)
        self.assertIn("Normalized status: submitted", output)

    def test_cli_prints_only_sanitized_http_diagnostics(self) -> None:
        error = KlingHttpError(
            http_status=429,
            provider_code=42901,
            provider_message="rate limited",
            provider_request_id="request-429",
            retry_after="30",
        )
        with patch("sys.argv", ["kling_submit_test", "--confirm"]), patch(
            "app.cli.kling_submit_test.KlingProvider"
        ) as provider_class, patch("app.cli.kling_submit_test.print") as print_mock:
            provider_class.return_value.submit_scene.side_effect = error

            self.assertEqual(main(), 1)

        output = "\n".join(call.args[0] for call in print_mock.call_args_list)
        self.assertIn("HTTP status: 429", output)
        self.assertIn("Kling code: 42901", output)
        self.assertIn("Kling message: rate limited", output)
        self.assertIn("Kling request ID: request-429", output)
        self.assertIn("Retry-After: 30", output)
        self.assertNotIn("KLING_API_KEY", output)
        self.assertNotIn("cheerful garden", output)
