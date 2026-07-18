from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError, field_validator

from .kling_client import KlingClientError
from .kling_schema_diagnostics import shape_summary, validation_details


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
    ) -> None:
        super().__init__(message)
        self.validation_errors = validation_errors
        self.response_shape = response_shape


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
    external_id: StrictStr


class KlingCreateTaskResponse(BaseModel):
    """Exact documented Create Task response, including provider-level errors."""

    model_config = ConfigDict(extra="forbid")

    code: StrictInt
    message: StrictStr
    request_id: StrictStr
    data: KlingCreateTaskData | None = None

    @classmethod
    def parse(cls, payload: object) -> "KlingCreateTaskResponse":
        if not isinstance(payload, dict):
            raise KlingMalformedResponseError("Kling Create Task response must be a JSON object.")
        if payload.get("code") == 0:
            raw_data = payload.get("data")
            if raw_data is None:
                raise KlingMalformedResponseError(
                    "Kling Create Task success response is missing the documented data object.",
                    validation_errors=("data: missing field [missing]",),
                    response_shape=shape_summary(payload),
                )
            if isinstance(raw_data, dict) and not raw_data.get("id"):
                raise KlingMalformedResponseError(
                    "Kling Create Task success response is missing data.id.",
                    validation_errors=("data.id: missing field [missing]",),
                    response_shape=shape_summary(payload),
                )
        try:
            response = cls.model_validate(payload)
        except ValidationError as error:
            raise KlingMalformedResponseError(
                "Kling Create Task response does not match the documented response contract.",
                validation_errors=validation_details(error),
                response_shape=shape_summary(payload),
            ) from error

        if response.code != 0:
            raise KlingProviderApiError(response.code, response.message, response.request_id)
        if response.data is None:
            raise KlingMalformedResponseError(
                "Kling Create Task success response is missing data.",
                validation_errors=("data: missing field [missing]",),
                response_shape=shape_summary(payload),
            )
        return response


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
    message: StrictStr
    create_time: StrictInt
    update_time: StrictInt
    external_id: StrictStr
    outputs: list[dict[str, Any]]
    billing: list[dict[str, Any]]

    @field_validator("outputs", "billing")
    @classmethod
    def entries_must_be_objects(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if any(not isinstance(entry, dict) for entry in value):
            raise ValueError("Query Task output and billing entries must be objects.")
        return value


class KlingQueryTasksResponse(BaseModel):
    """Exact documented root structure for Query Task."""

    model_config = ConfigDict(extra="forbid")

    code: StrictInt
    message: StrictStr
    request_id: StrictStr
    data: list[KlingTaskData]

    @classmethod
    def parse(cls, payload: object) -> "KlingQueryTasksResponse":
        if not isinstance(payload, dict):
            raise KlingMalformedResponseError("Kling Query Task response must be a JSON object.")
        try:
            response = cls.model_validate(payload)
        except ValidationError as error:
            raise KlingMalformedResponseError(
                "Kling Query Task response does not match the documented response contract.",
                validation_errors=validation_details(error),
                response_shape=shape_summary(payload),
            ) from error
        if response.code != 0:
            raise KlingProviderApiError(response.code, response.message, response.request_id)
        return response


class KlingApiError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str | int | None = None
    message: str = Field(min_length=1)
