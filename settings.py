"""Plugin config readers (defaults match plugin.yaml config_schema)."""

from __future__ import annotations

from typing import Any, Callable, Optional

_get_config: Optional[Callable[[str, Any], Any]] = None

DEFAULT_MAX_CALENDAR_DAYS = 31
# Absolute ceiling (matches plugin.yaml config_schema.maximum and CALL_BUDGET).
ABSOLUTE_MAX_CALENDAR_DAYS = 31


def bind_config(getter: Optional[Callable[[str, Any], Any]]) -> None:
    """Called from register(ctx) so settings are read at call time."""
    global _get_config
    _get_config = getter


def _cfg(key: str, default: Any) -> Any:
    if _get_config is None:
        return default
    try:
        value = _get_config(key, default)
    except Exception:
        return default
    return default if value is None else value


def _as_int(
    value: Any,
    default: int,
    *,
    minimum: int = 1,
    maximum: int = ABSOLUTE_MAX_CALENDAR_DAYS,
) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, n))


def max_calendar_days() -> int:
    """Hard upper bound for edinet_search_filings (plugin config; model args may only tighten)."""
    return _as_int(
        _cfg("max_calendar_days", DEFAULT_MAX_CALENDAR_DAYS),
        DEFAULT_MAX_CALENDAR_DAYS,
        maximum=ABSOLUTE_MAX_CALENDAR_DAYS,
    )
