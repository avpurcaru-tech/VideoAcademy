import io
import socket
from email.message import Message
import unittest
from typing import Any
from urllib.error import HTTPError

from app.providers import KlingHttpClient, KlingHttpError, KlingTimeoutError


class KlingHttpClientTests(unittest.TestCase):
    def test_post_json_serializes_payload_and_headers(self) -> None:
        opener = RecordingOpener([FakeResponse(200, b'{"code": 0}')])
        client = KlingHttpClient(api_key="test-key", base_url="https://api-singapore.klingai.com", opener=opener)

        response = client.post_json("/text-to-video/kling-3.0", {"prompt": "test"})

        self.assertEqual(response, {"code": 0})
        self.assertEqual(opener.requests[0].method, "POST")
        self.assertEqual(opener.requests[0].get_header("Content-type"), "application/json")

    def test_get_json_serializes_query_parameters(self) -> None:
        opener = RecordingOpener([FakeResponse(200, b'{"code": 0, "data": []}')])
        client = KlingHttpClient(api_key="test-key", base_url="https://api-singapore.klingai.com", opener=opener)

        client.get_json("/tasks", params={"external_task_ids": "external id"})

        self.assertEqual(opener.requests[0].method, "GET")
        self.assertEqual(
            opener.requests[0].full_url,
            "https://api-singapore.klingai.com/tasks?external_task_ids=external+id",
        )

    def test_submit_429_exposes_only_sanitized_provider_envelope_and_does_not_retry(self) -> None:
        opener = RecordingOpener(
            [
                http_error(
                    429,
                    (
                        b'{"code": 42901, "message": '
                        b'"rate limited: secret-key never expose this prompt", "request_id": "req-429"}'
                    ),
                    retry_after="30",
                )
            ]
        )
        delays: list[float] = []
        client = KlingHttpClient(api_key="secret-key", opener=opener, max_retries=2, sleeper=delays.append)

        with self.assertRaises(KlingHttpError) as context:
            client.post_json("/text-to-video/kling-3.0", {"prompt": "never expose this prompt"})

        error = context.exception
        self.assertEqual(error.http_status, 429)
        self.assertEqual(error.provider_code, 42901)
        self.assertEqual(error.provider_message, "rate limited: [redacted] [redacted]")
        self.assertEqual(error.provider_request_id, "req-429")
        self.assertEqual(error.retry_after, "30")
        self.assertEqual(len(opener.requests), 1)
        self.assertEqual(delays, [])
        self.assertNotIn("secret-key", str(error))
        self.assertNotIn("never expose this prompt", str(error))
        self.assertNotIn("secret-key", error.provider_message)
        self.assertNotIn("never expose this prompt", error.provider_message)

    def test_submit_429_with_malformed_json_has_safe_diagnostics(self) -> None:
        opener = RecordingOpener([http_error(429, b"not-json", content_type="text/plain")])
        client = KlingHttpClient(api_key="test-key", opener=opener, sleeper=lambda _: None)

        with self.assertRaises(KlingHttpError) as context:
            client.post_json("/text-to-video/kling-3.0", {"prompt": "test"})

        self.assertEqual(context.exception.http_status, 429)
        self.assertIsNone(context.exception.provider_message)
        self.assertEqual(context.exception.content_type, "text/plain")

    def test_submit_404_is_not_interpreted_as_invalid_credentials(self) -> None:
        opener = RecordingOpener([http_error(404, b'{"message": "not found"}')])
        client = KlingHttpClient(api_key="test-key", opener=opener, sleeper=lambda _: None)

        with self.assertRaises(KlingHttpError) as context:
            client.post_json("/text-to-video/kling-3.0", {"prompt": "test"})

        self.assertEqual(context.exception.http_status, 404)
        self.assertEqual(context.exception.provider_message, "not found")

    def test_get_json_retries_rate_limits(self) -> None:
        opener = RecordingOpener(
            [http_error(429, b'{"message": "rate limited"}'), FakeResponse(200, b'{"code": 0}')]
        )
        delays: list[float] = []
        client = KlingHttpClient(api_key="test-key", opener=opener, max_retries=1, sleeper=delays.append)

        self.assertEqual(client.get_json("/tasks", {"external_task_ids": "one"}), {"code": 0})
        self.assertEqual(delays, [1])

    def test_get_json_preserves_sanitized_http_error_fields(self) -> None:
        opener = RecordingOpener(
            [
                http_error(
                    429,
                    b'{"code": 42901, "message": "rate limited", "request_id": "request-429"}',
                    retry_after="30",
                )
            ]
        )
        client = KlingHttpClient(api_key="test-key", opener=opener, max_retries=0, sleeper=lambda _: None)

        with self.assertRaises(KlingHttpError) as context:
            client.get_json("/tasks", {"task_ids": "task-01"})

        error = context.exception
        self.assertEqual(error.http_status, 429)
        self.assertEqual(error.provider_code, 42901)
        self.assertEqual(error.provider_message, "rate limited")
        self.assertEqual(error.provider_request_id, "request-429")
        self.assertEqual(error.retry_after, "30")

    def test_post_json_preserves_timeout_handling_without_retry(self) -> None:
        opener = RecordingOpener([socket.timeout("slow")])
        client = KlingHttpClient(api_key="test-key", opener=opener, max_retries=1, sleeper=lambda _: None)

        with self.assertRaises(KlingTimeoutError):
            client.post_json("/text-to-video/kling-3.0", {"prompt": "test"})


class RecordingOpener:
    def __init__(self, responses: list[object]) -> None:
        self._responses = responses
        self.requests: list[Any] = []

    def __call__(self, request: Any, timeout: float) -> Any:
        self.requests.append(request)
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body
        self.headers = Message()

    def getcode(self) -> int:
        return self.status

    def read(self) -> bytes:
        return self._body

    def close(self) -> None:
        return None


def http_error(
    status: int,
    body: bytes,
    content_type: str = "application/json",
    retry_after: str | None = None,
) -> HTTPError:
    headers = Message()
    headers["Content-Type"] = content_type
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return HTTPError("https://api-singapore.klingai.com/test", status, "error", headers, io.BytesIO(body))
