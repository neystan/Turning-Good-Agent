from dataclasses import dataclass, field
from typing import Any, Protocol


_JSON_TYPE_MAP: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


@dataclass(slots=True)
class ToolResult:
    """保存工具执行结果。"""

    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseTool(Protocol):
    """定义工具必须提供的最小接口。"""

    name: str
    description: str
    input_schema: dict[str, Any]
    parallel_safe: bool

    async def run(self, args: dict[str, Any]) -> ToolResult:
        """执行工具并返回文本结果。"""


def schema_type(schema: dict[str, Any]) -> str | None:
    """读取 JSON Schema 的非 null 类型。"""
    raw_type = schema.get("type")
    if isinstance(raw_type, list):
        return next((item for item in raw_type if item != "null"), None)
    return raw_type


def cast_value(value: Any, schema: dict[str, Any]) -> Any:
    """按 JSON Schema 做安全类型转换。"""
    expected_type = schema_type(schema)
    if expected_type == "string":
        return value if value is None else str(value)
    if expected_type == "integer" and isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return value
    if expected_type == "number" and isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return value
    if expected_type == "boolean" and isinstance(value, str):
        lowered = value.lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
        return value
    if expected_type == "array" and isinstance(value, list) and isinstance(schema.get("items"), dict):
        return [cast_value(item, schema["items"]) for item in value]
    if expected_type == "object" and isinstance(value, dict):
        return cast_args(value, schema)
    return value


def cast_args(args: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """按工具参数 schema 归一化参数。"""
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        return args
    return {
        key: cast_value(value, properties[key]) if isinstance(properties.get(key), dict) else value
        for key, value in args.items()
    }


def validate_value(value: Any, schema: dict[str, Any], path: str) -> list[str]:
    """校验单个参数值。"""
    expected_type = schema_type(schema)
    nullable = schema.get("nullable", False) or (
        isinstance(schema.get("type"), list) and "null" in schema.get("type", [])
    )
    if value is None and nullable:
        return []
    label = path or "parameter"
    if expected_type == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        return [f"{label} should be integer"]
    if expected_type == "number" and (
        not isinstance(value, _JSON_TYPE_MAP["number"]) or isinstance(value, bool)
    ):
        return [f"{label} should be number"]
    if expected_type in _JSON_TYPE_MAP and expected_type not in {"integer", "number"}:
        if not isinstance(value, _JSON_TYPE_MAP[expected_type]):
            return [f"{label} should be {expected_type}"]

    errors: list[str] = []
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{label} must be one of {schema['enum']}")
    if expected_type in {"integer", "number"}:
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{label} must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{label} must be <= {schema['maximum']}")
    if expected_type == "string":
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{label} must be at least {schema['minLength']} chars")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{label} must be at most {schema['maxLength']} chars")
    if expected_type == "array":
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{label} must have at least {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{label} must be at most {schema['maxItems']} items")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(validate_value(item, item_schema, f"{label}[{index}]"))
    if expected_type == "object":
        errors.extend(validate_args(value, schema, path))
    return errors


def validate_args(args: Any, schema: dict[str, Any], path: str = "") -> list[str]:
    """校验工具参数对象。"""
    if not isinstance(args, dict):
        return [f"parameters must be object, got {type(args).__name__}"]
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        properties = {}
    errors: list[str] = []
    for key in schema.get("required", []):
        if key not in args:
            errors.append(f"missing required {key}")
    for key, value in args.items():
        child_schema = properties.get(key)
        if isinstance(child_schema, dict):
            errors.extend(validate_value(value, child_schema, key if not path else f"{path}.{key}"))
    return errors
