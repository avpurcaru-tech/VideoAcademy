import unittest
from unittest.mock import patch

from app.cli.kling_auth_check import main


class KlingAuthenticationCliTests(unittest.TestCase):
    def test_cli_does_not_make_an_http_request_without_a_documented_endpoint(self) -> None:
        with patch("app.cli.kling_auth_check.print") as print_mock:
            self.assertEqual(main(), 2)

        print_mock.assert_called_once()
        self.assertIn("No HTTP request was made", print_mock.call_args.args[0])
