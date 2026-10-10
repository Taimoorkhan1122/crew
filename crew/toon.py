from __future__ import annotations

import math
import re
from decimal import Decimal
from typing import Any


_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_NUMERIC = re.compile(r"^[+-]?[0-9]+(?:\.[0-9]+)?(?:e[+-]?[0-9]+)?$", re.IGNORECASE | re.ASCII)


def _normalize(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        if isinstance(value, str) and any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise ValueError("TOON strings must contain Unicode scalar values")
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("TOON object keys must be strings")
        return {key: _normalize(item) for key, item in value.items()}
    raise TypeError(f"unsupported TOON value: {type(value).__name__}")


def _quoted(value: str) -> str:
    escaped = []
    for char in value:
        code = ord(char)
        if char == "\\":
            escaped.append("\\\\")
        elif char == '"':
            escaped.append('\\"')
        elif char == "\n":
            escaped.append("\\n")
        elif char == "\r":
            escaped.append("\\r")
        elif char == "\t":
            escaped.append("\\t")
        elif code < 0x20:
            escaped.append(f"\\u{code:04x}")
        else:
            escaped.append(char)
    return '"' + "".join(escaped) + '"'


def _string(value: str, *, root: bool = False) -> str:
    must_quote = (
        not value
        or value != value.strip(" \t")
        or value in {"true", "false", "null"}
        or bool(_NUMERIC.fullmatch(value))
        or any(char in value for char in ':"\\[]{},')
        or any(ord(char) < 0x20 for char in value)
        or value.startswith(("-", "#"))
        or (root and value.startswith("\ufeff"))
    )
    return _quoted(value) if must_quote else value


def _key(value: str) -> str:
    return value if _KEY.fullmatch(value) else _quoted(value)


def _number(value: int | float) -> str:
    if isinstance(value, int):
        return str(value)
    if value == 0:
        return "0"
    rendered = repr(value).lower()
    if 1e-6 <= abs(value) < 1e21:
        fixed = format(Decimal(rendered), "f")
        return fixed.rstrip("0").rstrip(".") if "." in fixed else fixed
    if "e" in rendered:
        mantissa, exponent = rendered.split("e", 1)
        sign = "+" if not exponent.startswith(("+", "-")) else ""
        return f"{mantissa}e{sign}{int(exponent)}"
    return rendered[:-2] if rendered.endswith(".0") else rendered


def _primitive(value: Any, *, root: bool = False) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return _string(value, root=root)
    if isinstance(value, (int, float)):
        return _number(value)
    raise TypeError("expected a TOON primitive")


def _fields(rows: list[dict]) -> list[tuple[str, list | None]] | None:
    if not rows or any(not row for row in rows):
        return None
    keys = list(rows[0])
    if any(set(row) != set(keys) for row in rows[1:]):
        return None
    result = []
    for key in keys:
        values = [row[key] for row in rows]
        if all(value is None or isinstance(value, (str, bool, int, float)) for value in values):
            result.append((key, None))
        elif all(isinstance(value, dict) and value for value in values):
            nested = _fields(values)
            if nested is None:
                return None
            result.append((key, nested))
        else:
            return None
    return result


def _keyed_fields(value: dict) -> list[tuple[str, list | None]] | None:
    if len(value) < 2 or any(not isinstance(item, dict) for item in value.values()):
        return None
    return _fields(list(value.values()))


def _header_fields(fields: list[tuple[str, list | None]]) -> str:
    return ",".join(_key(name) if nested is None else f"{_key(name)}{{{_header_fields(nested)}}}" for name, nested in fields)


def _row_values(row: dict, fields: list[tuple[str, list | None]]) -> list[str]:
    values = []
    for name, nested in fields:
        value = row[name]
        if nested is None:
            values.append(_primitive(value))
        else:
            values.extend(_row_values(value, nested))
    return values


def _emit_object(lines: list[str], value: dict, depth: int, prefix: str = "") -> None:
    if not value and prefix:
        lines.append("  " * depth + prefix.rstrip())
        return
    first = True
    for key, item in value.items():
        lead = "  " * depth + (prefix if first else " " * len(prefix))
        first = False
        encoded_key = _key(key)
        if item is None or isinstance(item, (str, bool, int, float)):
            lines.append(f"{lead}{encoded_key}: {_primitive(item)}")
        elif isinstance(item, dict):
            keyed = _keyed_fields(item)
            if keyed is not None:
                lines.append(f"{lead}{encoded_key}[{len(item)}:]{{{_header_fields(keyed)}}}:")
                for entry, row in item.items():
                    lines.append("  " * (depth + 1 + bool(prefix)) + f"{_key(entry)}: " + ",".join(_row_values(row, keyed)))
            else:
                lines.append(f"{lead}{encoded_key}:")
                _emit_object(lines, item, depth + 1 + bool(prefix))
        elif isinstance(item, list):
            _emit_array(lines, item, depth + bool(prefix), encoded_key, lead)
        else:
            raise TypeError(f"unsupported TOON value: {type(item).__name__}")


def _emit_array(lines: list[str], value: list, depth: int, key: str | None, lead: str = "") -> None:
    prefix = lead + (f"{key}" if key else "")
    if not value:
        lines.append(f"{prefix}: []" if key else f"{lead}[]" if lead else "[]")
        return
    if all(item is None or isinstance(item, (str, bool, int, float)) for item in value):
        cells = ",".join(_primitive(item, root=key is None) for item in value)
        lines.append(f"{prefix}[{len(value)}]: {cells}")
        return
    fields = (
        _fields(value)
        if (key is not None or not lead) and all(isinstance(item, dict) for item in value)
        else None
    )
    if fields is not None:
        header = _header_fields(fields)
        lines.append(f"{prefix}[{len(value)}]{{{header}}}:")
        for row in value:
            lines.append("  " * (depth + 1) + ",".join(_row_values(row, fields)))
        return
    lines.append(f"{prefix}[{len(value)}]:")
    for item in value:
        item_depth = depth + 1
        if item is None or isinstance(item, (str, bool, int, float)):
            lines.append("  " * item_depth + "- " + _primitive(item))
        elif isinstance(item, dict):
            _emit_object(lines, item, item_depth, prefix="- ")
        elif isinstance(item, list):
            _emit_array(lines, item, item_depth, None, "  " * item_depth + "- ")
        else:
            raise TypeError(f"unsupported TOON value: {type(item).__name__}")


def encode(value: Any) -> str:
    """Encode JSON-shaped data using comma-delimited TOON, without a final LF."""
    normalized = _normalize(value)
    lines: list[str] = []
    if isinstance(normalized, dict):
        keyed = _keyed_fields(normalized)
        if keyed is not None:
            lines.append(f"[{len(normalized)}:]{{{_header_fields(keyed)}}}:")
            for entry, row in normalized.items():
                lines.append("  " + f"{_key(entry)}: " + ",".join(_row_values(row, keyed)))
        else:
            _emit_object(lines, normalized, 0)
    elif isinstance(normalized, list):
        _emit_array(lines, normalized, 0, None)
    else:
        lines.append(_primitive(normalized, root=True))
    return "\n".join(lines)
