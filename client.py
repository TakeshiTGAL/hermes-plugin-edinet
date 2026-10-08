"""HTTPS client for the EDINET API v2 (https://api.edinet-fsa.go.jp/).

Standard library only. The API key is read from EDINET_API_KEY and sent only to
api.edinet-fsa.go.jp as the Subscription-Key query parameter. Request URLs and
the key are never put into exceptions, log lines, or tool results.

EDINET often returns HTTP 200 with a JSON error envelope (StatusCode or
metadata.status). Those bodies are classified as failures and never returned
as success to callers.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from .errors import ApiKeyError, EdinetError
from .version import __version__

API_HOST = "api.edinet-fsa.go.jp"
API_BASE = f"https://{API_HOST}/api/v2"
API_KEY_ENV = "EDINET_API_KEY"
SIGNUP_URL = "https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/WEEK0060.html"
GUIDE_URL = "https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/WEEK0060.html"
USER_AGENT = f"hermes-plugin-edinet/{__version__}"
TIMEOUT_SECONDS = 30
# Per-call wall budget. A cold max_calendar_days=31 span needs ~31 list GETs plus
# ≥1s pauses; slow links or retries can still exhaust the budget before the span ends.
CALL_BUDGET_SECONDS = 180
RETRY_DELAY_SECONDS = 1.0
MAX_RETRIES = 2  # at most two retries after the first attempt (429/5xx/network_error)
MAX_RESPONSE_BYTES = 25 * 1024 * 1024  # 25 MiB
# Process-wide minimum gap between live EDINET GETs (list or document), including
# parallel tool calls (WZEK0030 III 3.2 short-burst restriction).
MIN_REQUEST_INTERVAL_SECONDS = 1.0
_request_lock = threading.Lock()
_last_request_mono = 0.0

API_KEY_STEPS = (
    "How to get a free EDINET API key: "
    f"1) open the FSA EDINET API materials index ({SIGNUP_URL}, WEEK0060); "
    "2) follow the linked API specification PDF for account creation and key issue; "
    f"3) set {API_KEY_ENV}=<your key> in the Hermes home .env (or paste it when "
    "`hermes plugins install` asks), then start a new session. "
    f"Guide: {GUIDE_URL}"
)


def _api_key() -> str:
    value = os.environ.get(API_KEY_ENV, "").strip()
    if not value:
        raise ApiKeyError(
            f"{API_KEY_ENV} is not set, so EDINET cannot be queried.",
            next_step=API_KEY_STEPS,
        )
    return value


def scrub_secret(text: str, secret: str = "") -> str:
    """Remove the API key (and Subscription-Key=… query fragments) from text."""
    text = str(text)
    secret = secret or os.environ.get(API_KEY_ENV, "").strip()
    if secret:
        text = text.replace(secret, "***")
        text = text.replace(urllib.parse.quote(secret, safe=""), "***")
    text = re.sub(r"(?i)Subscription-Key=[^&\s\"']+", "Subscription-Key=***", text)
    return text


def scrub(text: str, secret: str = "") -> str:
    """Remove request URLs and the API key from error/network text before it is shown."""
    text = re.sub(r"https?://\S+", "<request URL removed>", str(text))
    return scrub_secret(text, secret)


# --- time budget ----------------------------------------------------------------

_deadline: contextvars.ContextVar[Optional[float]] = contextvars.ContextVar(
    "edinet_deadline", default=None
)


@contextlib.contextmanager
def budget(seconds: float = CALL_BUDGET_SECONDS):
    token = _deadline.set(time.monotonic() + seconds)
    try:
        yield
    finally:
        _deadline.reset(token)


def _remaining() -> Optional[float]:
    deadline = _deadline.get()
    return None if deadline is None else deadline - time.monotonic()


def _out_of_time() -> EdinetError:
    return EdinetError(
        f"EDINET did not answer within {CALL_BUDGET_SECONDS} seconds. "
        "Try again with a shorter date range.",
        kind="timeout",
        next_step="Retry later, or narrow date_from/date_to.",
    )


# --- JSON error envelopes (HTTP 200 with error body) ----------------------------


def _status_int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _not_found_next_step(*, resource: str) -> str:
    if resource == "list":
        # Future dates are rejected locally before any request; reaching here means
        # EDINET had no list for that calendar day (retention ended, holiday, etc.).
        return (
            "EDINET has no document list for that file date "
            "(outside retention, non-business day, or not published). "
            "Try another file date."
        )
    return "Confirm the doc_id from edinet_search_filings, then retry."


def _envelope_next_step(kind: str, status: int, *, resource: str) -> str:
    if kind == "not_found":
        return _not_found_next_step(resource=resource)
    if status == 400 and resource == "list":
        return (
            "EDINET returned 400 for this file date. The search fails for the whole "
            "date range. Only a 404 list is skipped as outside retention."
        )
    if status == 400:
        # The id already passed the 8-character check. A 400 here is not a length error.
        return (
            "EDINET returned 400. This is not treated as a missing document. "
            "Only 404 is not_found. Check the doc_id from edinet_search_filings "
            "and the document type."
        )
    return "Wait a few seconds and retry; avoid burst traffic."


def _envelope_kind(status: int) -> str:
    """Map EDINET envelope status to tool kind.

    Outside retention on the live API is 404. A 400 fails the call (and a list
    search fails the whole date range). It is not skipped as not_found.
    """
    if status == 404:
        return "not_found"
    if status == 429:
        return "rate_limited"
    return "http_error"


def _classify_edinet_body(raw: bytes, *, api_key: str = "", resource: str = "document") -> None:
    """Raise if ``raw`` is an EDINET JSON error envelope. No-op for binary bodies."""
    if not raw:
        return
    stripped = raw.lstrip()
    if not stripped.startswith((b"{", b"[")):
        return
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return

    if "StatusCode" in data:
        code = _status_int(data.get("StatusCode"))
        if code is None:
            raise EdinetError(
                "EDINET returned a JSON error envelope with an unreadable StatusCode.",
                kind="bad_response",
                next_step="Retry later; confirm the API key and request.",
            )
        if code == 200:
            return
        detail = scrub(str(data.get("message") or ""), api_key)
        if code in (401, 403):
            raise ApiKeyError(
                f"EDINET rejected the API key in {API_KEY_ENV} "
                f"(StatusCode {code}"
                + (f": {detail}" if detail else "")
                + ").",
                next_step=API_KEY_STEPS,
                status=code,
            )
        kind = _envelope_kind(code)
        msg = f"EDINET returned StatusCode {code}."
        if detail:
            msg += f" ({detail})"
        raise EdinetError(
            msg,
            kind=kind,
            status=code,
            next_step=_envelope_next_step(kind, code, resource=resource),
        )

    meta = data.get("metadata")
    if isinstance(meta, dict) and "status" in meta:
        status = _status_int(meta.get("status"))
        if status is None:
            raise EdinetError(
                "EDINET returned metadata.status that is not a number.",
                kind="bad_response",
                next_step="Retry later.",
            )
        if status == 200:
            return
        detail = scrub(str(meta.get("message") or data.get("message") or ""), api_key)
        if status in (401, 403):
            raise ApiKeyError(
                f"EDINET rejected the API key in {API_KEY_ENV} "
                f"(metadata.status {status}"
                + (f": {detail}" if detail else "")
                + ").",
                next_step=API_KEY_STEPS,
                status=status,
            )
        kind = _envelope_kind(status)
        msg = f"EDINET returned metadata.status {status}."
        if detail:
            msg += f" ({detail})"
        raise EdinetError(
            msg,
            kind=kind,
            status=status,
            next_step=_envelope_next_step(kind, status, resource=resource),
        )


# --- transport ------------------------------------------------------------------


class _SameHostRedirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects only to https://api.edinet-fsa.go.jp (key is in the query)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parts = urllib.parse.urlsplit(newurl)
        if parts.scheme != "https" or parts.hostname != API_HOST:
            raise EdinetError(
                "EDINET redirected the request to another host or to plain HTTP; "
                "the plugin refused to follow it so the API key stays with EDINET.",
                kind="http_error",
                next_step="Try again later.",
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_SameHostRedirects)


def _timeout() -> float:
    timeout = TIMEOUT_SECONDS
    remaining = _remaining()
    if remaining is not None:
        if remaining < 1:
            raise _out_of_time()
        timeout = min(timeout, remaining)
    return timeout


def _read_capped(response) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise EdinetError(
                f"EDINET response exceeded the {MAX_RESPONSE_BYTES // (1024 * 1024)} MiB size cap.",
                kind="bad_response",
                next_step="Retry with a smaller document type, or report if it persists.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _pace_live_request() -> None:
    """Enforce MIN_REQUEST_INTERVAL_SECONDS between live GETs process-wide."""
    global _last_request_mono
    with _request_lock:
        now = time.monotonic()
        wait = MIN_REQUEST_INTERVAL_SECONDS - (now - _last_request_mono)
        if wait > 0:
            time.sleep(wait)
        _last_request_mono = time.monotonic()


def _http_get_bytes(
    path: str, params: dict, api_key: str, *, resource: str = "document"
) -> bytes:
    """GET one path under /api/v2 and return raw bytes. path starts with /."""
    _pace_live_request()
    query = urllib.parse.urlencode({**params, "Subscription-Key": api_key})
    url = API_BASE + path + "?" + query
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
    )
    try:
        with _opener.open(request, timeout=_timeout()) as response:
            raw = _read_capped(response)
    except EdinetError:
        raise
    except urllib.error.HTTPError as error:
        body = b""
        with contextlib.suppress(Exception):
            body = error.read() or b""
        # JSON error envelopes (even on non-200 HTTP) take precedence.
        _classify_edinet_body(body, api_key=api_key, resource=resource)
        raise _http_error(error.code, body, api_key, resource=resource) from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        if _remaining() is not None and (_remaining() or 0) < 1:
            raise _out_of_time() from None
        reason = scrub(getattr(error, "reason", error), api_key)
        raise EdinetError(
            f"Could not reach api.edinet-fsa.go.jp ({reason}). "
            "Check the network connection and try again.",
            kind="network_error",
            next_step="Check connectivity, then retry.",
        ) from None

    _classify_edinet_body(raw, api_key=api_key, resource=resource)
    return raw


def _http_error(
    code: int, body: bytes, api_key: str, *, resource: str = "document"
) -> EdinetError:
    detail = ""
    with contextlib.suppress(Exception):
        text = body.decode("utf-8", errors="replace")[:200]
        detail = scrub(text, api_key)
    if code == 401 or code == 403:
        return ApiKeyError(
            f"EDINET rejected the API key in {API_KEY_ENV} (HTTP {code}).",
            next_step=API_KEY_STEPS,
            status=code,
        )
    if code == 404:
        if resource == "list":
            msg = "EDINET returned HTTP 404 for that file date."
        else:
            msg = "EDINET returned HTTP 404 for that document."
        return EdinetError(
            msg,
            kind="not_found",
            status=code,
            next_step=_not_found_next_step(resource=resource),
        )
    if code == 400:
        if resource == "list":
            msg = "EDINET returned HTTP 400 for that file date."
        else:
            msg = "EDINET returned HTTP 400 for that document."
        return EdinetError(
            msg,
            kind="http_error",
            status=code,
            next_step=_envelope_next_step("http_error", code, resource=resource),
        )
    msg = f"EDINET answered HTTP {code}."
    if detail:
        msg += f" ({detail})"
    kind = "rate_limited" if code == 429 else "http_error"
    return EdinetError(
        msg,
        kind=kind,
        status=code,
        next_step="Wait a few seconds and retry; avoid burst traffic.",
    )


def _should_retry(error: EdinetError) -> bool:
    """Retry only transient failures: HTTP 429/5xx and network errors (not bad_response)."""
    if error.kind == "rate_limited":
        return True
    if error.kind == "http_error" and error.status is not None and error.status >= 500:
        return True
    if error.kind == "network_error":
        return True
    return False


def _with_retries(fetch):
    last: Optional[EdinetError] = None
    for attempt in range(1 + MAX_RETRIES):
        try:
            return fetch()
        except EdinetError as error:
            last = error
            if attempt < MAX_RETRIES and _should_retry(error):
                time.sleep(RETRY_DELAY_SECONDS)
                continue
            raise
    raise last or EdinetError("EDINET request failed.")


def fetch_documents_list(date: str) -> dict:
    """GET /documents.json?date=YYYY-MM-DD&type=2 and return the decoded JSON."""
    api_key = _api_key()

    def once():
        raw = _http_get_bytes(
            "/documents.json",
            {"date": date, "type": "2"},
            api_key,
            resource="list",
        )
        # Classify again after decode path (defense in depth for callers that
        # patch _http_get_bytes to return fixture bytes directly).
        _classify_edinet_body(raw, api_key=api_key, resource="list")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise EdinetError(
                "EDINET returned a non-JSON list response.",
                kind="bad_response",
                next_step="Try again later.",
            ) from None
        if not isinstance(payload, dict):
            raise EdinetError(
                "EDINET list response was not a JSON object.",
                kind="bad_response",
                next_step="Try again later.",
            )
        return payload

    return _with_retries(once)


def fetch_document_bytes(doc_id: str, doc_type: int) -> bytes:
    """GET /documents/{docID}?type={1-5}. type 2=PDF, 1=ZIP, 5=CSV ZIP."""
    if doc_type not in (1, 2, 3, 4, 5):
        raise EdinetError(
            f"Unsupported document type {doc_type}. Use 1 (ZIP), 2 (PDF), or 5 (CSV ZIP).",
            kind="bad_request",
            next_step="Pass type 1, 2, or 5.",
        )
    api_key = _api_key()
    path = f"/documents/{urllib.parse.quote(doc_id, safe='')}"

    def once():
        raw = _http_get_bytes(path, {"type": str(doc_type)}, api_key)
        _classify_edinet_body(raw, api_key=api_key)
        return raw

    return _with_retries(once)
