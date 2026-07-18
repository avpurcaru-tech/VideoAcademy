import unittest
from unittest.mock import patch

from app.cli.kling_query_by_id_test import main
from app.config import KlingGenerationConfigurationError
from app.models import GenerationTask, GenerationTaskStatus, VideoArtifact
from app.providers import (
    KlingHttpError,
    KlingMalformedResponseError,
    KlingProviderApiError,
    KlingProviderContractError,
    KlingTaskNotFoundError,
)


class KlingQueryByIdCliTests(unittest.TestCase):
    def test_cli_queries_once_and_prints_only_sanitized_task_fields(self) -> None:
        task = GenerationTask(
            request_id=None,
            external_task_id="907223632122871878",
            provider_name="kling",
            provider_status="succeeded",
            normalized_status=GenerationTaskStatus.SUCCEEDED,
            external_correlation_id="external-01",
            artifacts=[VideoArtifact(artifact_id="video-01", url="https://example.test/video.mp4")],
            provider_metadata={"task_message": "completed"},
        )
        with patch("sys.argv", ["kling_query_by_id_test", "--task-id", "907223632122871878"]), patch(
            "app.cli.kling_query_by_id_test.KlingProvider"
        ) as provider_class, patch("app.cli.kling_query_by_id_test.sync_task_record") as registry_sync, patch(
            "app.cli.kling_query_by_id_test.print"
        ) as print_mock:
            provider_class.return_value.get_task_by_id.return_value = task

            self.assertEqual(main(), 0)

        provider_class.return_value.get_task_by_id.assert_called_once_with("907223632122871878")
        registry_sync.assert_called_once_with(task)
        output = "\n".join(call.args[0] for call in print_mock.call_args_list)
        self.assertIn("Kling task ID: 907223632122871878", output)
        self.assertIn("External correlation ID: external-01", output)
        self.assertIn("Normalized status: succeeded", output)
        self.assertIn("Task message: completed", output)
        self.assertIn("Video URL: https://example.test/video.mp4", output)
        self.assertNotIn("Authorization", output)
        self.assertIsNone(task.artifacts[0].watermark_url)

    def test_cli_prints_sanitized_http_diagnostics_for_401_403_404_and_429(self) -> None:
        for status in (401, 403, 404, 429):
            with self.subTest(status=status):
                error = KlingHttpError(
                    http_status=status,
                    provider_code=1000 + status,
                    provider_message="safe provider message",
                    provider_request_id=f"request-{status}",
                    retry_after="30" if status == 429 else None,
                )
                output = self._run_with_error(error)

                self.assertIn(f"HTTP status: {status}", output)
                self.assertIn(f"Kling code: {1000 + status}", output)
                self.assertIn("Kling message: safe provider message", output)
                self.assertIn(f"Kling request ID: request-{status}", output)
                if status == 429:
                    self.assertIn("Retry-After: 30", output)
                else:
                    self.assertNotIn("Retry-After", output)

    def test_cli_prints_documented_provider_error(self) -> None:
        output = self._run_with_error(KlingProviderApiError(1001, "provider error", "request-01"))

        self.assertIn("Kling code: 1001", output)
        self.assertIn("Kling message: provider error", output)
        self.assertIn("Kling request ID: request-01", output)

    def test_cli_handles_domain_errors_without_details(self) -> None:
        cases = [
            (
                KlingTaskNotFoundError("missing"),
                "Task not found for Kling task ID: 907223632122871878",
            ),
            (
                KlingMalformedResponseError("malformed"),
                "Kling returned a response that does not match the documented task schema.",
            ),
            (
                KlingProviderContractError("unknown status"),
                "Kling returned an unsupported provider-contract value.",
            ),
            (
                KlingGenerationConfigurationError("unsupported resolution"),
                "Invalid Kling configuration: unsupported resolution",
            ),
        ]
        for error, expected_output in cases:
            with self.subTest(error=type(error).__name__):
                self.assertIn(expected_output, self._run_with_error(error))

    def test_cli_prints_sanitized_schema_mismatch_details(self) -> None:
        error = KlingMalformedResponseError(
            "malformed",
            validation_errors=("data.0.billing: expected array, received null [list_type]",),
            response_shape=("root: object", "root.data: array[1]", "root.data[0].billing: null"),
        )

        output = self._run_with_error(error)

        self.assertIn("Kling returned a response that does not match the documented task schema.", output)
        self.assertIn("Schema mismatch: data.0.billing: expected array, received null [list_type]", output)
        self.assertIn("Response shape: root.data[0].billing: null", output)

    def test_cli_hides_secrets_billing_and_unexpected_exception_details(self) -> None:
        output = self._run_with_error(
            RuntimeError("secret-key Authorization: Bearer value billing=[sensitive]")
        )

        self.assertEqual(output, "Kling task query failed due to an unexpected local error.")
        self.assertNotIn("secret-key", output)
        self.assertNotIn("billing", output)

    @staticmethod
    def _run_with_error(error: Exception) -> str:
        with patch("sys.argv", ["kling_query_by_id_test", "--task-id", "907223632122871878"]), patch(
            "app.cli.kling_query_by_id_test.KlingProvider"
        ) as provider_class, patch("app.cli.kling_query_by_id_test.print") as print_mock:
            provider_class.return_value.get_task_by_id.side_effect = error

            return_code = main()

        if return_code != 1:
            raise AssertionError("The query CLI must return 1 for a query error.")
        return "\n".join(call.args[0] for call in print_mock.call_args_list)
