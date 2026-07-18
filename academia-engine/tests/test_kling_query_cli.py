import unittest
from unittest.mock import patch

from app.cli.kling_query_test import main
from app.providers import KlingHttpError


class KlingQueryCliTests(unittest.TestCase):
    def test_external_id_cli_uses_the_same_sanitized_http_diagnostics(self) -> None:
        error = KlingHttpError(
            http_status=429,
            provider_code=42901,
            provider_message="rate limited",
            provider_request_id="request-429",
            retry_after="30",
        )
        with patch("sys.argv", ["kling_query_test", "--external-id", "external-01"]), patch(
            "app.cli.kling_query_test.KlingProvider"
        ) as provider_class, patch("app.cli.kling_query_test.print") as print_mock:
            provider_class.return_value.get_task_by_external_id.side_effect = error

            self.assertEqual(main(), 1)

        output = "\n".join(call.args[0] for call in print_mock.call_args_list)
        self.assertEqual(
            output,
            "HTTP status: 429\nKling code: 42901\nKling message: rate limited\n"
            "Kling request ID: request-429\nRetry-After: 30",
        )
