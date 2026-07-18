from copy import deepcopy
import unittest

from app.providers import KlingMalformedResponseError, KlingProvider
from app.providers.kling_schema_diagnostics import shape_summary


SAFE_QUERY_FIXTURE = {
    "code": 0,
    "message": "safe-message",
    "request_id": "request-secret-01",
    "data": [
        {
            "id": "task-secret-01",
            "status": "succeeded",
            "message": "task-message-secret",
            "create_time": 1781080778802,
            "update_time": 1781080794151,
            "external_id": "external-secret-01",
            "outputs": [],
            "billing": [{"amount": "999.99"}],
        }
    ],
}


class KlingSchemaDiagnosticsTests(unittest.TestCase):
    def test_missing_field_diagnostic_has_path_but_no_values(self) -> None:
        fixture = deepcopy(SAFE_QUERY_FIXTURE)
        del fixture["data"][0]["outputs"]

        error = self._query_error(fixture)

        self.assertIn("data.0.outputs: missing field [missing]", error.validation_errors)
        self._assert_no_values(error)

    def test_null_list_diagnostic_reports_only_expected_and_actual_types(self) -> None:
        fixture = deepcopy(SAFE_QUERY_FIXTURE)
        fixture["data"][0]["billing"] = None

        error = self._query_error(fixture)

        self.assertIn("data.0.billing: expected array, received null [list_type]", error.validation_errors)
        self._assert_no_values(error)

    def test_string_timestamp_diagnostic_reports_types_only(self) -> None:
        fixture = deepcopy(SAFE_QUERY_FIXTURE)
        fixture["data"][0]["update_time"] = "1781080794151"

        error = self._query_error(fixture)

        self.assertIn("data.0.update_time: expected integer, received string [int_type]", error.validation_errors)
        self._assert_no_values(error)

    def test_extra_field_diagnostic_reports_only_path(self) -> None:
        fixture = deepcopy(SAFE_QUERY_FIXTURE)
        fixture["data"][0]["some_field"] = "do-not-print-this"

        error = self._query_error(fixture)

        self.assertIn("data.0.some_field: unexpected field [extra]", error.validation_errors)
        self._assert_no_values(error)

    def test_nested_video_output_validation_has_prefixed_path(self) -> None:
        fixture = deepcopy(SAFE_QUERY_FIXTURE)
        fixture["data"][0]["outputs"] = [
            {
                "type": "video",
                "id": "video-secret",
                "watermark_url": "https://example.test/watermark-secret.mp4",
                "duration": "10",
            }
        ]

        error = self._query_error(fixture)

        self.assertIn("data.0.outputs.0.url: missing field [missing]", error.validation_errors)
        self.assertIn("data.0.outputs.0: object", error.response_shape)
        self.assertIn("data.0.outputs.0.type: string", error.response_shape)
        self._assert_no_values(error)

    def test_shape_summary_is_bounded_by_depth_and_entry_count(self) -> None:
        deep_value: object = {"leaf": "secret"}
        for _ in range(10):
            deep_value = {"nested": deep_value}
        large_payload = {f"field_{index}": deep_value for index in range(100)}

        summary = shape_summary(large_payload)

        self.assertLessEqual(len(summary), 41)
        self.assertTrue(any("truncated" in entry for entry in summary))
        self.assertNotIn("secret", "\n".join(summary))

    @staticmethod
    def _query_error(fixture: dict[str, object]) -> KlingMalformedResponseError:
        with unittest.TestCase().assertRaises(KlingMalformedResponseError) as context:
            KlingProvider(client=FakeKlingClient(fixture)).get_task_by_id("task-secret-01")
        return context.exception

    def _assert_no_values(self, error: KlingMalformedResponseError) -> None:
        output = "\n".join((*error.validation_errors, *error.response_shape))
        for secret in (
            "task-secret-01",
            "external-secret-01",
            "request-secret-01",
            "task-message-secret",
            "999.99",
            "https://",
            "do-not-print-this",
        ):
            self.assertNotIn(secret, output)


class FakeKlingClient:
    def __init__(self, response: dict[str, object]) -> None:
        self._response = response

    def get_json(self, path: str, params: dict[str, str]) -> dict[str, object]:
        return self._response
