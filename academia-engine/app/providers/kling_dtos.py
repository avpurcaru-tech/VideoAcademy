from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError, field_validator, model_validator

from .kling_client import KlingClientError
from .kling_schema_diagnostics import query_shape_summary, shape_summary, submit_shape_summary, validation_details


class KlingSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution: Literal["720p"] = "720p"
    aspect_ratio: Literal["16:9"] = "16:9"
    duration: int
    audio: Literal["off"] = "off"
    multi_shot: bool = True


class KlingWatermarkInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False


class KlingOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    callback_url: str | None = None
    external_task_id: str = Field(min_length=1)
    watermark_info: KlingWatermarkInfo


class KlingTextToVideoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1)
    settings: KlingSettings
    options: KlingOptions

    def to_payload(self) -> dict[str, object]:
        """Serialize the documented request, omitting unspecified optional fields."""
        return self.model_dump(mode="json", exclude_none=True)


class KlingMalformedResponseError(KlingClientError):
    """Raised when a Kling response does not match the documented contract."""

    def __init__(
        self,
        message: str,
        validation_errors: tuple[str, ...] = (),
        response_shape: tuple[str, ...] = (),
        provider_task_id: str | None = None,
        external_correlation_id: str | None = None,
        http_status: int | None = None,
        provider_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.validation_errors = validation_errors
        self.response_shape = response_shape
        self.provider_task_id = provider_task_id
        self.external_correlation_id = external_correlation_id
        self.http_status = http_status
        self.provider_code = provider_code


class KlingProviderContractError(KlingClientError):
    """Raised for documented-shape responses with unsupported provider values."""


class KlingProviderApiError(KlingClientError):
    """Raised when Kling returns a non-zero documented provider code."""

    def __init__(self, code: int, message: str, request_id: str) -> None:
        super().__init__(f"Kling provider error {code}: {message}")
        self.code = code
        self.message = message
        self.request_id = request_id


class KlingTaskNotFoundError(KlingClientError):
    """Raised when Query Task has no result for the requested external ID."""


class KlingCreateTaskData(BaseModel):
    """Exact `data` object from the documented Create Task success response."""

    model_config = ConfigDict(extra="forbid")

    id: StrictStr
    status: StrictStr
    create_time: StrictInt
    update_time: StrictInt
    external_id: StrictStr | None = None


class KlingCreateTaskResponse(BaseModel):
    """Exact documented Create Task response, including provider-level errors."""

    model_config = ConfigDict(extra="forbid")

    code: StrictInt
    message: StrictStr
    request_id: StrictStr
    data: KlingCreateTaskData | None = None

    @classmethod
    def parse(cls, payload: object) -> "KlingCreateTaskResponse":
        task_id = _submit_task_id(payload)
        external_id = _submit_external_id(payload)
        status = getattr(payload, "http_status", None)
        code = payload.get("code") if isinstance(payload, dict) and type(payload.get("code")) is int else None
        diagnostics = dict(provider_task_id=task_id, external_correlation_id=external_id,
                           http_status=status, provider_code=code)
        if not isinstance(payload, dict):
            raise KlingMalformedResponseError("Kling Create Task response must be a JSON object.",
                                               response_shape=submit_shape_summary(payload), **diagnostics)
        if code is not None and code != 0 and isinstance(payload.get("message"), str) and isinstance(payload.get("request_id"), str):
            raise KlingProviderApiError(code, payload["message"], payload["request_id"])
        if payload.get("code") == 0:
            raw_data = payload.get("data")
            if raw_data is None:
                raise KlingMalformedResponseError(
                    "Kling Create Task success response is missing the documented data object.",
                    validation_errors=("data: missing field [missing]",),
                    response_shape=submit_shape_summary(payload), **diagnostics,
                )
            if task_id is None:
                raise KlingMalformedResponseError(
                    "Kling Create Task success response is missing data.id.",
                    validation_errors=("data.id: missing field [missing]",),
                    response_shape=submit_shape_summary(payload), **diagnostics,
                )
        normalized = dict(payload)
        raw_data = normalized.get("data")
        if isinstance(raw_data, dict):
            normalized_data = dict(raw_data)
            if "id" not in normalized_data and task_id is not None:
                normalized_data["id"] = task_id
            normalized_data.pop("task_id", None)
            if "external_id" not in normalized_data and external_id is not None:
                normalized_data["external_id"] = external_id
            normalized["data"] = normalized_data
        normalized.pop("id", None)
        normalized.pop("external_id", None)
        try:
            response = cls.model_validate(normalized)
        except ValidationError as error:
            raise KlingMalformedResponseError(
                "Kling Create Task response does not match the documented response contract.",
                validation_errors=validation_details(error),
                response_shape=submit_shape_summary(payload), **diagnostics,
            ) from error

        if response.code != 0:
            raise KlingProviderApiError(response.code, response.message, response.request_id)
        if response.data is None:
            raise KlingMalformedResponseError(
                "Kling Create Task success response is missing data.",
                validation_errors=("data: missing field [missing]",),
                response_shape=submit_shape_summary(payload), **diagnostics,
            )
        return response


def _safe_identifier(value: object) -> str | None:
    if not isinstance(value, str) or not value or not value.replace("_", "").replace("-", "").isalnum():
        return None
    return value


def _submit_task_id(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if isinstance(data, dict):
        for field in ("id", "task_id"):
            task_id = _safe_identifier(data.get(field))
            if task_id:
                return task_id
    return _safe_identifier(payload.get("id"))


def _submit_external_id(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if isinstance(data, dict):
        external_id = _safe_identifier(data.get("external_id"))
        if external_id:
            return external_id
    return _safe_identifier(payload.get("external_id"))


class KlingVideoOutput(BaseModel):
    """The only output type mapped to a generic video artifact in this sprint."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["video"]
    id: StrictStr
    url: StrictStr
    watermark_url: StrictStr | None = None
    duration: StrictStr

    @field_validator("id", "url", "duration")
    @classmethod
    def required_strings_must_not_be_empty(cls, value: str) -> str:
        if not value:
            raise ValueError("Video output fields must not be empty.")
        return value

    def duration_seconds(self) -> float:
        try:
            duration = Decimal(self.duration)
        except InvalidOperation as error:
            raise KlingMalformedResponseError("Kling video output duration is invalid.") from error
        if not duration.is_finite() or duration < 0:
            raise KlingMalformedResponseError("Kling video output duration is invalid.")
        return float(duration)


class KlingTaskData(BaseModel):
    """Exact task fields declared in the supplied Query Task response contract."""

    model_config = ConfigDict(extra="forbid")

    id: StrictStr
    status: StrictStr
    message: StrictStr | None = None
    create_time: StrictInt
    update_time: StrictInt
    external_id: StrictStr | None = None
    outputs: list[dict[str, Any]] | None = None
    billing: list[dict[str, Any]] | None = None

    @field_validator("outputs", "billing")
    @classmethod
    def entries_must_be_objects(cls, value: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
        if value is None:
            return None
        if any(not isinstance(entry, dict) for entry in value):
            raise ValueError("Query Task output and billing entries must be objects.")
        return value

    @model_validator(mode="after")
    def succeeded_requires_outputs(self) -> "KlingTaskData":
        if self.status == "succeeded" and self.outputs is None:
            raise ValueError("Succeeded Query Task response requires outputs.")
        return self


class KlingQueryTasksResponse(BaseModel):
    """Exact documented root structure for Query Task."""

    model_config = ConfigDict(extra="forbid")

    code: StrictInt
    message: StrictStr
    request_id: StrictStr
    data: list[KlingTaskData]

    @classmethod
    def parse(cls, payload: object) -> "KlingQueryTasksResponse":
        task_id = _query_task_id(payload)
        status = getattr(payload, "http_status", None)
        code = payload.get("code") if isinstance(payload, dict) and type(payload.get("code")) is int else None
        diagnostics = dict(provider_task_id=task_id, http_status=status, provider_code=code)
        if not isinstance(payload, dict):
            raise KlingMalformedResponseError("Kling Query Task response must be a JSON object.",
                response_shape=query_shape_summary(payload), **diagnostics)
        if code is not None and code != 0 and isinstance(payload.get("message"), str) and isinstance(payload.get("request_id"), str):
            raise KlingProviderApiError(code, payload["message"], payload["request_id"])
        normalized = dict(payload)
        if isinstance(normalized.get("data"), dict):
            normalized["data"] = [normalized["data"]]
        try:
            response = cls.model_validate(normalized)
        except ValidationError as error:
            raise KlingMalformedResponseError(
                "Kling Query Task response does not match the documented response contract.",
                validation_errors=validation_details(error),
                response_shape=query_shape_summary(payload), **diagnostics,
            ) from error
        if response.code != 0:
            raise KlingProviderApiError(response.code, response.message, response.request_id)
        return response


def _query_task_id(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if isinstance(data, dict):
        return _safe_identifier(data.get("id"))
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return _safe_identifier(data[0].get("id"))
    return None


class KlingApiError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str | int | None = None
    message: str = Field(min_length=1)
