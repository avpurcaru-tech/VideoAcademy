from collections.abc import Callable

from app.providers import KlingHttpError, KlingMalformedResponseError, KlingProviderApiError


def print_http_diagnostics(error: KlingHttpError, emit: Callable[[str], None]) -> None:
    """Print only the sanitized diagnostics retained by the HTTP client."""
    emit(f"HTTP status: {error.http_status}")
    if error.provider_code is not None:
        emit(f"Kling code: {error.provider_code}")
    if error.provider_message:
        emit(f"Kling message: {error.provider_message}")
    if error.provider_request_id:
        emit(f"Kling request ID: {error.provider_request_id}")
    if error.retry_after:
        emit(f"Retry-After: {error.retry_after}")


def print_provider_error(error: KlingProviderApiError, emit: Callable[[str], None]) -> None:
    """Print documented provider-envelope fields without a raw response body."""
    emit(f"Kling code: {error.code}")
    emit(f"Kling message: {error.message}")
    emit(f"Kling request ID: {error.request_id}")


def print_schema_mismatch(error: KlingMalformedResponseError, emit: Callable[[str], None]) -> None:
    """Print validation paths and type-only response shape without values."""
    emit("Kling returned a response that does not match the documented task schema.")
    for detail in error.validation_errors:
        emit(f"Schema mismatch: {detail}")
    for entry in error.response_shape:
        emit(f"Response shape: {entry}")
