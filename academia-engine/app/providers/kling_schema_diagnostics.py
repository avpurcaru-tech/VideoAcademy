from collections.abc import Iterable
from typing import Any

from pydantic import ValidationError

_MAX_DEPTH = 5
_MAX_ENTRIES = 40
_MISSING = object()


def validation_details(
    error: ValidationError,
    location_prefix: tuple[str | int, ...] = (),
) -> tuple[str, ...]:
    """Convert Pydantic errors to paths and JSON types without retaining values."""
    details: list[str] = []
    for item in error.errors():
        location = location_prefix + tuple(item["loc"])
        path = _format_path(location)
        category = item["type"]
        actual = item.get("input", _MISSING)
        if category == "missing":
            details.append(f"{path}: missing field [missing]")
        elif category == "extra_forbidden":
            details.append(f"{path}: unexpected field [extra]")
        else:
            details.append(
                f"{path}: expected {_expected_description(category)}, received {_json_type(actual)} "
                f"[{category}]"
            )
    return tuple(details[:_MAX_ENTRIES])


def shape_summary(payload: object, root_path: str = "root") -> tuple[str, ...]:
    """Describe JSON shape only, with fixed depth and entry limits."""
    entries: list[str] = []

    def visit(value: object, path: str, depth: int) -> None:
        if len(entries) >= _MAX_ENTRIES:
            return
        type_name = _json_type(value)
        if isinstance(value, list):
            entries.append(f"{path}: array[{len(value)}]")
        else:
            entries.append(f"{path}: {type_name}")
        if depth >= _MAX_DEPTH:
            if isinstance(value, (dict, list)) and len(entries) < _MAX_ENTRIES:
                entries.append(f"{path}: truncated at maximum depth")
            return
        if isinstance(value, dict):
            for key, child in value.items():
                if len(entries) >= _MAX_ENTRIES:
                    return
                visit(child, f"{path}.{key}", depth + 1)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                if len(entries) >= _MAX_ENTRIES:
                    return
                visit(child, f"{path}[{index}]", depth + 1)

    visit(payload, root_path, 0)
    if len(entries) >= _MAX_ENTRIES:
        entries.append(f"{root_path}: truncated at maximum entry count")
    return tuple(entries[: _MAX_ENTRIES + 1])


def submit_shape_summary(payload: object) -> tuple[str, ...]:
    """Return the fixed, value-free allowlist for Create Task diagnostics."""
    entries = [f"root: {_json_type(payload)}"]
    if not isinstance(payload, dict):
        return tuple(entries)
    for field in ("code", "message", "request_id", "data"):
        entries.append(f"{field}: {_json_type(payload[field]) if field in payload else 'missing'}")
    data = payload.get("data")
    if isinstance(data, dict):
        for field in ("id", "status", "external_id", "create_time", "update_time"):
            entries.append(f"data.{field}: {_json_type(data[field]) if field in data else 'missing'}")
    elif isinstance(data, list):
        entries.append(f"data item count: {len(data)}")
        if data and isinstance(data[0], dict):
            for field in sorted(data[0]):
                if field.lower() in {"authorization", "billing", "prompt"}:
                    continue
                entries.append(f"data[0].{field}: {_json_type(data[0][field])}")
        elif data:
            entries.append(f"data[0]: {_json_type(data[0])}")
    return tuple(entries)


def query_shape_summary(payload: object) -> tuple[str, ...]:
    """Describe only allowlisted Query Task fields, never bodies, URLs, or billing."""
    entries = [f"root: {_json_type(payload)}"]
    if not isinstance(payload, dict):
        return tuple(entries)
    for field in ("code", "message", "request_id", "data"):
        entries.append(f"{field}: {_json_type(payload[field]) if field in payload else 'missing'}")
    data = payload.get("data")
    if isinstance(data, dict):
        _query_task_fields(entries, data, "data")
    elif isinstance(data, list):
        entries.append(f"data item count: {len(data)}")
        if data and isinstance(data[0], dict):
            _query_task_fields(entries, data[0], "data[0]")
    return tuple(entries)


def _query_task_fields(entries: list[str], task: dict[object, object], prefix: str) -> None:
    for field in ("id", "status", "message", "external_id", "create_time", "update_time", "outputs"):
        entries.append(f"{prefix}.{field}: {_json_type(task[field]) if field in task else 'missing'}")
    outputs = task.get("outputs")
    if isinstance(outputs, list):
        entries.append(f"{prefix}.outputs item count: {len(outputs)}")
        if outputs and isinstance(outputs[0], dict):
            for field in ("type", "id", "duration"):
                entries.append(
                    f"{prefix}.outputs[0].{field}: {_json_type(outputs[0][field]) if field in outputs[0] else 'missing'}"
                )


def _format_path(location: Iterable[str | int]) -> str:
    return ".".join(str(part) for part in location) or "root"


def _expected_description(category: str) -> str:
    if "int" in category:
        return "integer"
    if "float" in category or "decimal" in category:
        return "number"
    if "list" in category:
        return "array"
    if "dict" in category or "model" in category:
        return "object"
    if "string" in category or "str" in category:
        return "string"
    if "literal" in category:
        return "documented literal"
    return "documented constraint"


def _json_type(value: object) -> str:
    if value is _MISSING:
        return "missing"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"
