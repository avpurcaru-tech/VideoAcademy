import json
import logging
import os
import socket
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


class KlingClientError(RuntimeError):
    """Base error for Kling HTTP connectivity failures."""


class KlingAuthenticationError(KlingClientError):
    """Raised when Kling rejects the configured credentials."""


class KlingTimeoutError(KlingClientError):
    """Raised when the health check times out after all retries."""


class KlingAuthenticationProbeUnavailableError(KlingClientError):
    """Raised while no current documented standalone authentication probe exists."""


class KlingHttpError(KlingClientError):
    """Sanitized diagnostics for a non-successful Kling HTTP response."""

    def __init__(
        self,
        http_status: int,
        provider_code: int | str | None = None,
        provider_message: str | None = None,
        provider_request_id: str | None = None,
        retry_after: str | None = None,
        content_type: str | None = None,
    ) -> None:
        super().__init__(f"Kling request failed with HTTP {http_status}")
        self.http_status = http_status
        self.provider_code = provider_code
        self.provider_message = provider_message
        self.provider_request_id = provider_request_id
        self.retry_after = retry_after
        self.content_type = content_type


class KlingHttpClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.klingai.com",
        timeout_seconds: float = 10,
        max_retries: int = 2,
        opener: Callable[..., Any] = urlopen,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api_key = api_key or self._load_api_key()
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._opener = opener
        self._sleeper = sleeper

    def get_account_usage(self) -> dict[str, Any]:
        raise KlingAuthenticationProbeUnavailableError(
            "Standalone Kling authentication probing is unavailable until a current official endpoint is configured."
        )

    def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            url=f"{self._base_url}/{path.lstrip('/')}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        prompt = payload.get("prompt")
        sensitive_values = (self._api_key, prompt) if isinstance(prompt, str) else (self._api_key,)
        return self._send_json(
            request,
            allow_retries=False,
            sensitive_values=sensitive_values,
        )

    def get_json(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        query_string = urlencode(params)
        request = Request(
            url=f"{self._base_url}/{path.lstrip('/')}?{query_string}",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="GET",
        )
        return self._send_json(request, allow_retries=True)

    def _send_json(
        self,
        request: Request,
        allow_retries: bool = True,
        sensitive_values: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        for attempt in range(self._max_retries + 1):
            try:
                response = self._opener(request, timeout=self._timeout_seconds)
                return self._read_response(response)
            except HTTPError as error:
                try:
                    diagnostic = self._http_error_from_response(
                        status_code=error.code,
                        body=error.read(),
                        headers=error.headers,
                        sensitive_values=sensitive_values,
                    )
                    if allow_retries and self._should_retry(error.code, attempt):
                        self._retry(attempt, error.code)
                        continue
                    logger.error("Kling request failed with HTTP %s", error.code)
                    raise diagnostic from error
                finally:
                    error.close()
            except (TimeoutError, socket.timeout, URLError) as error:
                if allow_retries and attempt < self._max_retries:
                    self._retry(attempt, "network error")
                    continue
                logger.error("Kling request timed out or could not connect")
                raise KlingTimeoutError("Kling request timed out or could not connect") from error

        raise KlingClientError("Kling request failed")

    def health_check(self) -> dict[str, Any]:
        """Backward-compatible alias that remains disabled with the standalone probe."""
        return self.get_account_usage()

    @staticmethod
    def _load_api_key() -> str:
        api_key = os.environ.get("KLING_API_KEY")
        if api_key:
            return api_key

        for directory in (Path.cwd(), *Path.cwd().parents):
            env_file = directory / ".env"
            if not env_file.is_file():
                continue
            for line in env_file.read_text(encoding="utf-8").splitlines():
                key, separator, value = line.partition("=")
                if key.strip() == "KLING_API_KEY" and separator:
                    parsed_value = value.strip().strip('"').strip("'")
                    if parsed_value:
                        return parsed_value

        raise ValueError("KLING_API_KEY is required")

    def _read_response(self, response: Any) -> dict[str, Any]:
        try:
            status_code = getattr(response, "status", response.getcode())
            if not 200 <= status_code < 300:
                logger.error("Kling request failed with HTTP %s", status_code)
                raise self._http_error_from_response(
                    status_code=status_code,
                    body=response.read(),
                    headers=getattr(response, "headers", None),
                )

            response_body = response.read().decode("utf-8")
            return json.loads(response_body) if response_body else {}
        finally:
            response.close()

    @staticmethod
    def _http_error_from_response(
        status_code: int,
        body: bytes,
        headers: Any,
        sensitive_values: tuple[str, ...] = (),
    ) -> KlingHttpError:
        content_type = headers.get("Content-Type") if headers is not None else None
        retry_after = headers.get("Retry-After") if headers is not None else None
        provider_code: int | str | None = None
        provider_message: str | None = None
        provider_request_id: str | None = None
        try:
            envelope = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            envelope = None
        if isinstance(envelope, dict):
            code = envelope.get("code")
            message = envelope.get("message")
            request_id = envelope.get("request_id")
            provider_code = code if isinstance(code, (int, str)) and not isinstance(code, bool) else None
            provider_message = (
                KlingHttpClient._redact_sensitive_values(message, sensitive_values)
                if isinstance(message, str)
                else None
            )
            provider_request_id = request_id if isinstance(request_id, str) else None
        return KlingHttpError(
            http_status=status_code,
            provider_code=provider_code,
            provider_message=provider_message,
            provider_request_id=provider_request_id,
            retry_after=retry_after,
            content_type=content_type,
        )

    @staticmethod
    def _redact_sensitive_values(message: str, sensitive_values: tuple[str, ...]) -> str:
        sanitized_message = message
        for value in sensitive_values:
            if value:
                sanitized_message = sanitized_message.replace(value, "[redacted]")
        return sanitized_message

    def _should_retry(self, status_code: int, attempt: int) -> bool:
        return attempt < self._max_retries and (status_code == 429 or status_code >= 500)

    def _retry(self, attempt: int, reason: int | str) -> None:
        delay_seconds = 2**attempt
        logger.warning(
            "Kling request failed (%s); retrying in %s second(s)",
            reason,
            delay_seconds,
        )
        self._sleeper(delay_seconds)
