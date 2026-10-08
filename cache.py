"""Disk cache for EDINET list JSON under plugin-data/jp-edinet/lists/.

List responses are keyed by (API-key fingerprint, file date). Past dates are not
immutable (withdrawals, disclosure status, staff edits, viewing-period expiry),
so entries expire: today (JST) after 15 minutes, past dates after 24 hours.

Only successful list payloads (metadata.status 200 and results as a list) are
written. Cached JSON error envelopes are deleted on read. A wrong or rotated
API key uses a different fingerprint subdirectory, so prior successes cannot
mask an invalid key.

The directory comes from Hermes plugin_data_dir. If that cannot be loaded,
this module refuses. It does not fall back to HERMES_HOME or ~/.hermes.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from . import client
from .errors import EdinetError

PLUGIN_NAME = "jp-edinet"

JST = ZoneInfo("Asia/Tokyo")
TODAY_TTL_SECONDS = 15 * 60
PAST_TTL_SECONDS = 24 * 60 * 60
# type=5 CSV ZIPs reused by edinet_financials (saved PDF/ZIP via save stay until overwritten).
DOCS_CSV_TTL_SECONDS = 24 * 60 * 60


def plugin_root() -> Path:
    """Hermes plugin-data/jp-edinet. Refuse when the public helper cannot be loaded."""
    try:
        from plugins.plugin_storage import plugin_data_dir

        return Path(plugin_data_dir(PLUGIN_NAME))
    except Exception as exc:
        raise EdinetError(
            "Hermes plugin_data_dir is unavailable; refusing to read or write cache.",
            kind="access_denied",
            next_step=(
                "Run inside Hermes Agent so plugins.plugin_storage.plugin_data_dir "
                "works, then retry. This plugin does not write to ~/.hermes as a fallback."
            ),
        ) from exc


def lists_dir() -> Path:
    """Path for list JSON (mkdir of the day file only after write guard succeeds)."""
    return plugin_root() / "lists"


def docs_dir() -> Path:
    """Path for saved documents per API-key fingerprint (mkdir after write guard)."""
    return plugin_root() / "docs" / api_key_fingerprint()


def api_key_fingerprint() -> str:
    """Stable short id for the current EDINET_API_KEY (never the key itself)."""
    key = client._api_key()
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def cached_csv_flag(doc_id: str) -> Optional[str]:
    """csvFlag from the newest cached list that mentions doc_id, else None.

    Search stores the list. Financials uses it so a csvFlag of 0 is not
    reported as a missing document. A later file date wins.
    """
    root = lists_dir() / api_key_fingerprint()
    if not root.is_dir():
        return None
    best_day = ""
    best_flag: Optional[str] = None
    for path in root.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            continue
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            continue
        for row in results:
            if not isinstance(row, dict) or row.get("docID") != doc_id:
                continue
            flag = row.get("csvFlag")
            if flag is None:
                continue
            if path.stem >= best_day:
                best_day = path.stem
                best_flag = str(flag).strip()
    return best_flag


def _today_jst() -> date:
    return datetime.now(JST).date()


def _ttl_for(day: date) -> int:
    return TODAY_TTL_SECONDS if day == _today_jst() else PAST_TTL_SECONDS


def _list_path(day: date) -> Path:
    return lists_dir() / api_key_fingerprint() / f"{day.isoformat()}.json"


def _drop_path(day: date) -> Path:
    """Sidecar: previous non-zero row count after that day dropped to 0."""
    return lists_dir() / api_key_fingerprint() / f"{day.isoformat()}.drop"


def _status_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def is_error_envelope(payload: Any) -> bool:
    """True when JSON looks like an EDINET/APIM error body (must not be cached)."""
    if not isinstance(payload, dict):
        return False
    if "StatusCode" in payload:
        code = _status_int(payload.get("StatusCode"))
        return code is None or code != 200
    meta = payload.get("metadata")
    if isinstance(meta, dict) and "status" in meta:
        status = _status_int(meta.get("status"))
        return status is None or status != 200
    return False


def result_count(payload: Any) -> Optional[int]:
    """``metadata.resultset.count`` as an int, or None when it is missing."""
    if not isinstance(payload, dict):
        return None
    meta = payload.get("metadata")
    if not isinstance(meta, dict):
        return None
    resultset = meta.get("resultset")
    if not isinstance(resultset, dict) or "count" not in resultset:
        return None
    return _status_int(resultset.get("count"))


def is_success_list_payload(payload: Any) -> bool:
    """True when list JSON is cacheable.

    Requires metadata.status 200, results as a list, and
    metadata.resultset.count equal to the number of rows. A mismatch is not
    a successful day and must not be cached.
    """
    if not isinstance(payload, dict) or is_error_envelope(payload):
        return False
    meta = payload.get("metadata")
    if not isinstance(meta, dict) or "status" not in meta:
        return False
    if _status_int(meta.get("status")) != 200:
        return False
    results = payload.get("results")
    count = result_count(payload)
    return isinstance(results, list) and count is not None and count == len(results)


def read_cached_list(day: date) -> Optional[dict]:
    path = _list_path(day)
    if not path.is_file():
        return None
    age = time.time() - path.stat().st_mtime
    if age > _ttl_for(day):
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        with contextlib.suppress(OSError):
            path.unlink()
        return None
    if is_error_envelope(payload) or not is_success_list_payload(payload):
        with contextlib.suppress(OSError):
            path.unlink()
        return None
    return payload


def write_cached_list(day: date, payload: dict) -> None:
    """Write a successful list payload (requires Hermes write guard)."""
    if not is_success_list_payload(payload):
        raise EdinetError(
            "Refusing to cache an EDINET list error envelope or invalid list payload.",
            kind="bad_response",
            next_step="Retry the search; do not trust a failed list response.",
        )
    path = _list_path(day)
    # Lazy import: save imports cache at module load.
    from . import save

    save.assert_path_writable(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    fd, tmp = tempfile.mkstemp(prefix=".list-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(raw)
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _stored_row_count(path: Path) -> Optional[int]:
    """Row count in an existing list file, including one past its TTL."""
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not is_success_list_payload(payload):
        return None
    return len(payload["results"])


def _read_drop(day: date) -> Optional[int]:
    path = _drop_path(day)
    if not path.is_file():
        return None
    try:
        previous = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return previous if previous > 0 else None


def _write_drop(day: date, previous: int) -> None:
    path = _drop_path(day)
    from . import save

    save.assert_path_writable(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{previous}\n", encoding="utf-8")


def _clear_drop(day: date) -> None:
    with contextlib.suppress(OSError):
        _drop_path(day).unlink()


def _with_drop_note(day: date, payload: dict) -> dict:
    """Copy a zero-row list and note a prior non-zero count. The file stays raw JSON."""
    previous = _read_drop(day)
    results = payload.get("results")
    if previous is None or not isinstance(results, list) or results:
        return payload
    noted = dict(payload)
    noted["_count_drop_from"] = previous
    return noted


def _reject_uncacheable_list(payload: Any) -> None:
    if is_success_list_payload(payload):
        return
    results = payload.get("results") if isinstance(payload, dict) else None
    count = result_count(payload)
    if isinstance(results, list) and (count is None or count != len(results)):
        shown = "missing" if count is None else str(count)
        raise EdinetError(
            f"EDINET metadata.resultset.count ({shown}) does not match result rows "
            f"({len(results)}). This day was not treated as a successful list and was not cached.",
            kind="bad_response",
            next_step="Retry later. Do not treat this day as empty or complete.",
        )
    raise EdinetError(
        "EDINET list response was not a successful metadata.status 200 payload.",
        kind="bad_response",
        next_step="Retry later; confirm the API key and date.",
    )


def get_list_for_date(day: date, *, fetch=None) -> dict:
    """Return list JSON for one file date.

    On a cache miss, Hermes write guard runs before the list GET so a denied
    or unavailable write path never hits EDINET for that day. A count/row
    mismatch is not cached. A drop from a previous non-zero count to 0 is
    cached as the new list and flagged for a warning until a non-zero list returns.
    """
    cached = read_cached_list(day)
    if cached is not None:
        return _with_drop_note(day, cached)
    path = _list_path(day)
    previous = _stored_row_count(path)
    from . import save

    save.assert_path_writable(path)
    fetch = fetch or client.fetch_documents_list
    payload = fetch(day.isoformat())
    _reject_uncacheable_list(payload)
    write_cached_list(day, payload)
    new_len = len(payload["results"])
    if previous is not None and previous > 0 and new_len == 0:
        _write_drop(day, previous)
    elif new_len > 0:
        _clear_drop(day)
    return _with_drop_note(day, payload)


def iter_dates(date_from: date, date_to: date):
    if date_to < date_from:
        raise ValueError("date_to is before date_from")
    day = date_from
    while day <= date_to:
        yield day
        day += timedelta(days=1)


def calendar_day_count(date_from: date, date_to: date) -> int:
    return (date_to - date_from).days + 1
