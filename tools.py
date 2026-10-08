"""Tool handlers. Each returns a JSON string and never raises into Hermes."""

from __future__ import annotations

import contextlib
import json
import re
import time
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

from . import cache, citation, client, metrics, save, settings
from .errors import ApiKeyError, EdinetError

JST = ZoneInfo("Asia/Tokyo")

# EDINET docTypeCode groups verified against live list samples (e.g. 2024-06-25).
DOC_TYPE_GROUPS: dict[str, set[str]] = {
    "yuho": {"120"},
    "annual": {"120"},
    "quarterly": {"140"},
    "semiannual": {"160"},
    "extraordinary": {"180", "190"},  # 臨時 + 訂正臨時
    # 訂正有報/訂正四半期/訂正半期/訂正臨時 — not 135 確認書 or 136 訂正確認書
    "amendment": {"130", "150", "170", "190"},
}

STATUS_LABELS = {
    "withdrawalStatus": {
        "1": "withdrawal requested",
        "2": "withdrawn",
    },
    "docInfoEditStatus": {
        "1": "document details edited by EDINET staff",
        "2": "document details edit finalized",
    },
    "disclosureStatus": {
        "1": "disclosure viewing period ending soon",
        "2": "disclosure viewing period ended",
    },
}


def _dump(obj: Any) -> str:
    """Serialize tool output; strip API keys but keep citation portal URLs."""
    return client.scrub_secret(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))


def _guard(handler):
    def wrapped(args, **kwargs):
        try:
            with client.budget():
                return handler(args or {})
        except ApiKeyError as error:
            return _dump(error.to_dict())
        except EdinetError as error:
            return _dump(error.to_dict())
        except Exception as error:  # never crash the agent
            detail = client.scrub(f"{type(error).__name__}: {error}")
            return _dump(
                {
                    "error": f"Unexpected error in the EDINET plugin ({detail}).",
                    "kind": "internal_error",
                    "next_step": "Retry with simpler arguments; report if it persists.",
                }
            )

    wrapped.__name__ = handler.__name__
    wrapped.__doc__ = handler.__doc__
    return wrapped


_CALENDAR_DAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def _parse_day(value: str, field: str) -> date:
    """Accept only YYYY-MM-DD. Week dates and compact dates are not converted."""
    text = (value or "").strip()
    match = _CALENDAR_DAY.fullmatch(text)
    parsed: Optional[date] = None
    if match:
        year, month, day = (int(match.group(part)) for part in (1, 2, 3))
        try:
            parsed = date(year, month, day)
        except ValueError:
            parsed = None
    if parsed is None or parsed.isoformat() != text:
        raise EdinetError(
            f"{field} must be YYYY-MM-DD (got {value!r}).",
            kind="bad_request",
            next_step=(
                f"Pass {field} like 2024-06-25. "
                "Week dates (2024-W26-2) and compact dates (20240625) are rejected."
            ),
        )
    return parsed


def _normalize_sec(code: str) -> str:
    digits = re.sub(r"\D", "", code or "")
    return digits


def _sec_matches(doc_code: Optional[str], needle: str) -> bool:
    a = _normalize_sec(doc_code or "")
    b = _normalize_sec(needle)
    if not a or not b:
        return False
    if a == b:
        return True
    # EDINET often stores 5 digits (72030) while users pass 4 (7203).
    if len(b) == 4 and a.startswith(b):
        return True
    if len(a) == 4 and b.startswith(a):
        return True
    return False


_KNOWN_DOC_TYPE_CODES = frozenset().union(*DOC_TYPE_GROUPS.values())


def _expand_doc_types(aliases: Optional[Iterable[str]]) -> Optional[set[str]]:
    if not aliases:
        return None
    codes: set[str] = set()
    for alias in aliases:
        key = str(alias).strip().lower()
        if key.isdigit():
            if key not in _KNOWN_DOC_TYPE_CODES:
                raise EdinetError(
                    f"Unknown numeric docTypeCode {alias!r}.",
                    kind="bad_request",
                    next_step=(
                        "Use yuho, annual, quarterly, semiannual, extraordinary, "
                        "amendment, or a known code: "
                        + ", ".join(sorted(_KNOWN_DOC_TYPE_CODES))
                        + "."
                    ),
                )
            codes.add(key)
            continue
        group = DOC_TYPE_GROUPS.get(key)
        if not group:
            raise EdinetError(
                f"Unknown doc_types entry {alias!r}.",
                kind="bad_request",
                next_step=(
                    "Use yuho, annual, quarterly, semiannual, extraordinary, "
                    "amendment, or a known numeric docTypeCode."
                ),
            )
        codes |= group
    return codes


def _doc_warnings(doc: dict) -> list[str]:
    warnings: list[str] = []
    for field, mapping in STATUS_LABELS.items():
        value = str(doc.get(field) or "0")
        if value in mapping:
            warnings.append(mapping[value])
    parent = doc.get("parentDocID")
    if parent:
        warnings.append(f"amendment/child of parentDocID {parent}")
    desc = doc.get("docDescription") or ""
    if "訂正" in desc:
        warnings.append("description indicates a correction filing (訂正)")
    return warnings


def _row(doc: dict) -> dict[str, Any]:
    doc_id = doc.get("docID")
    row: dict[str, Any] = {
        "docID": doc_id,
        "filerName": doc.get("filerName"),
        "secCode": doc.get("secCode"),
        "edinetCode": doc.get("edinetCode"),
        "JCN": doc.get("JCN"),
        "docTypeCode": doc.get("docTypeCode"),
        "docDescription": doc.get("docDescription"),
        "periodStart": doc.get("periodStart"),
        "periodEnd": doc.get("periodEnd"),
        "submitDateTime": doc.get("submitDateTime"),
        "csvFlag": doc.get("csvFlag"),
        "pdfFlag": doc.get("pdfFlag"),
        "parentDocID": doc.get("parentDocID"),
        "warnings": _doc_warnings(doc),
    }
    if doc_id:
        row["filing_page"] = citation.filing_page_url(str(doc_id))
    return row


def _matches_filters(
    doc: dict,
    *,
    company_name: str,
    sec_code: str,
    edinet_code: str,
    corporate_number: str,
    type_codes: Optional[set[str]],
) -> bool:
    if company_name:
        name = doc.get("filerName") or ""
        if company_name.casefold() not in name.casefold():
            return False
    if sec_code and not _sec_matches(doc.get("secCode"), sec_code):
        return False
    if edinet_code:
        if (doc.get("edinetCode") or "").upper() != edinet_code.strip().upper():
            return False
    if corporate_number:
        jcn = re.sub(r"\D", "", str(doc.get("JCN") or ""))
        needle = re.sub(r"\D", "", corporate_number)
        if jcn != needle:
            return False
    if type_codes is not None:
        if str(doc.get("docTypeCode") or "") not in type_codes:
            return False
    return True


def _resolve_max_calendar_days(args: dict) -> tuple[int, list[str]]:
    """Effective max = min(arg, plugin config). Arg may only tighten; never raise the cap."""
    configured = settings.max_calendar_days()
    warnings: list[str] = []
    raw = args.get("max_calendar_days")
    if raw is None:
        return configured, warnings
    try:
        requested = int(raw)
    except (TypeError, ValueError) as exc:
        raise EdinetError(
            "max_calendar_days must be an integer.",
            kind="bad_request",
            next_step="Omit it (uses plugin config, default 31) or pass a positive integer.",
        ) from exc
    if requested < 1:
        raise EdinetError(
            "max_calendar_days must be at least 1.",
            kind="bad_request",
            next_step="Pass a positive integer.",
        )
    max_days = min(requested, configured)
    if requested > configured:
        warnings.append(
            f"max_calendar_days argument {requested} exceeds the plugin cap "
            f"({configured}); using {configured}."
        )
    return max_days, warnings


@_guard
def edinet_search_filings(args: dict) -> str:
    date_from = _parse_day(args.get("date_from", ""), "date_from")
    date_to = _parse_day(args.get("date_to", ""), "date_to")
    if date_to < date_from:
        raise EdinetError(
            "date_to is before date_from.",
            kind="bad_request",
            next_step="Swap the dates or widen the range.",
        )
    today_jst = datetime.now(JST).date()
    if date_from > today_jst or date_to > today_jst:
        raise EdinetError(
            "The date range includes a future file date (JST).",
            kind="bad_request",
            next_step="Set date_to to today or earlier (JST).",
        )

    max_days, cap_warnings = _resolve_max_calendar_days(args)

    span = cache.calendar_day_count(date_from, date_to)
    if span > max_days:
        next_from = date_from + timedelta(days=max_days)
        extra: dict[str, Any] = {
            "calendar_days": span,
            "max_calendar_days": max_days,
        }
        if cap_warnings:
            extra["warnings"] = list(cap_warnings)
        raise EdinetError(
            f"Date range is {span} calendar days; max allowed per call is {max_days}. "
            "The range was not truncated.",
            kind="range_too_large",
            next_step=(
                f"Call again with date_from={date_from.isoformat()} and "
                f"date_to={(date_from + timedelta(days=max_days - 1)).isoformat()}, "
                f"then continue from date_from={next_from.isoformat()} "
                f"to date_to={date_to.isoformat()}."
            ),
            extra=extra,
        )

    company_name = (args.get("company_name") or "").strip()
    sec_code = (args.get("sec_code") or "").strip()
    edinet_code = (args.get("edinet_code") or "").strip()
    corporate_number = (args.get("corporate_number") or "").strip()
    if not any([company_name, sec_code, edinet_code, corporate_number]):
        raise EdinetError(
            "Provide at least one of company_name, sec_code, edinet_code, or corporate_number.",
            kind="bad_request",
            next_step="Example: sec_code='7203' with date_from/date_to around the filing date.",
        )
    if corporate_number:
        corporate_digits = re.sub(r"\D", "", corporate_number)
        if len(corporate_digits) != 13:
            raise EdinetError(
                "corporate_number must be a 13-digit corporate number (法人番号).",
                kind="bad_request",
                next_step=(
                    "Pass 13 digits, for example 1180301018771. "
                    "A wrong length is refused before search, so it is not reported as no match."
                ),
            )
        corporate_number = corporate_digits

    type_codes = _expand_doc_types(args.get("doc_types"))
    raw_limit = args.get("limit", 50)
    if raw_limit is None:
        limit = 50
    else:
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError) as exc:
            raise EdinetError(
                "limit must be an integer from 1 to 200.",
                kind="bad_request",
                next_step="Omit limit (default 50) or pass an integer from 1 to 200.",
            ) from exc
        if isinstance(raw_limit, bool) or limit < 1 or limit > 200:
            raise EdinetError(
                "limit must be an integer from 1 to 200.",
                kind="bad_request",
                next_step="Omit limit (default 50) or pass an integer from 1 to 200. 0 is not treated as 1.",
            )

    matches: list[dict] = []
    days_fetched = 0
    days_cache_hit = 0
    days_listed_nonempty = 0
    days_list_unavailable: list[str] = []
    count_drops: list[tuple[str, int]] = []
    truncated = False
    date_scan_stopped_at: Optional[str] = None
    for day in cache.iter_dates(date_from, date_to):
        before = cache.read_cached_list(day)
        try:
            payload = cache.get_list_for_date(day)
        except EdinetError as exc:
            # Live outside-retention lists are HTTP/envelope 404. Skip that day.
            # 400 fails the whole range (it is not not_found).
            if exc.kind == "not_found":
                days_list_unavailable.append(day.isoformat())
                continue
            raise
        drop_from = payload.pop("_count_drop_from", None)
        if isinstance(drop_from, int) and drop_from > 0:
            count_drops.append((day.isoformat(), drop_from))
        if before is not None:
            days_cache_hit += 1
        else:
            # Live list pacing is process-wide in client._pace_live_request (≥1s).
            days_fetched += 1
        docs = payload.get("results") or []
        if isinstance(docs, dict):
            docs = docs.get("docs") or []
        if docs:
            days_listed_nonempty += 1

        def _row_matches(row: dict) -> bool:
            return _matches_filters(
                row,
                company_name=company_name,
                sec_code=sec_code,
                edinet_code=edinet_code,
                corporate_number=corporate_number,
                type_codes=type_codes,
            )

        stop = False
        for index, doc in enumerate(docs):
            if not isinstance(doc, dict) or not _row_matches(doc):
                continue
            matches.append(_row(doc))
            if len(matches) < limit:
                continue
            more_in_day = any(
                isinstance(later, dict) and _row_matches(later) for later in docs[index + 1 :]
            )
            # Hitting the limit on the last match of the last day is not a cut.
            if more_in_day or day < date_to:
                truncated = True
                date_scan_stopped_at = day.isoformat()
            stop = True
            break
        if stop:
            break

    result: dict[str, Any] = {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "calendar_days": span,
        "max_calendar_days": max_days,
        "limit": limit,
        "truncated": truncated,
        "count": len(matches),
        "filings": matches,
        "cache": {
            "days_fetched": days_fetched,
            "days_cache_hit": days_cache_hit,
            "days_list_unavailable": list(days_list_unavailable),
            "ttl": {
                "today_jst_seconds": cache.TODAY_TTL_SECONDS,
                "past_dates_seconds": cache.PAST_TTL_SECONDS,
            },
            "note": (
                "List responses are cached under a per-API-key fingerprint and file date "
                "with a TTL. A different or rotated key cannot read another key's cache. "
                "Past dates are not immutable (withdrawals, disclosure status, staff edits, "
                "viewing-period expiry), so cached past lists can become stale after 24 hours. "
                "Error envelopes are never cached. metadata.resultset.count must equal "
                "the number of rows or that day fails and is not cached. A 404 list "
                "(outside retention) is skipped and listed in days_list_unavailable. "
                "A 400 fails the whole date range. A day that had rows and then returns "
                "0 keeps a warning until a non-zero list comes back."
            ),
        },
        "source": {
            # One hit → that filing page; multiple hits → portal only (each row has filing_page).
            "citation": citation.edinet_citation(
                doc_id=str(matches[0]["docID"] or "") if len(matches) == 1 else ""
            ),
            "excerpt_note": citation.excerpt_note(
                doc_id=str(matches[0]["docID"] or "") if len(matches) == 1 else ""
            ),
            "api": "EDINET API v2 documents.json type=2",
        },
    }
    if date_scan_stopped_at is not None:
        result["date_scan_stopped_at"] = date_scan_stopped_at
    warnings = list(cap_warnings)
    for day_s, previous in count_drops:
        warnings.append(
            f"Warning: file date {day_s} list count dropped from {previous} to 0. "
            "This is not proof that every filing disappeared. "
            "The warning repeats until that file date returns a non-zero list."
        )
    if days_list_unavailable:
        shown = ", ".join(days_list_unavailable[:5])
        more = len(days_list_unavailable) - 5
        suffix = f" (+{more} more)" if more > 0 else ""
        warnings.append(
            "Skipped file dates with list not_found (outside retention or unknown): "
            f"{shown}{suffix}."
        )
    if company_name and matches:
        warnings.append(
            "company_name is a substring match on filerName; short names can include "
            "group companies. Prefer sec_code or edinet_code for a single issuer."
        )
    if warnings:
        result["warnings"] = warnings
    if truncated:
        result["message"] = (
            f"Stopped after {limit} matches (limit); later file dates in the range "
            "were not scanned. Raise limit (max 200) or narrow the date window."
        )
        result["next_step"] = (
            "Raise limit, or continue from date_from after date_scan_stopped_at."
        )
    elif not matches:
        if days_listed_nonempty == 0 and days_list_unavailable:
            result["message"] = (
                "No matching filings; every scanned file date returned list not_found "
                "(outside retention or unknown) or an empty list. "
                "The list API is by submission/file date, not period end."
            )
            result["next_step"] = (
                "Narrow date_from/date_to to dates still in EDINET retention "
                f"(unavailable days include {days_list_unavailable[0]}"
                + (
                    f" … {days_list_unavailable[-1]}"
                    if len(days_list_unavailable) > 1
                    else ""
                )
                + ")."
            )
        elif days_listed_nonempty == 0:
            result["message"] = (
                "No filings on these file dates (each day's list was empty). "
                "The list API is by submission/file date, not period end."
            )
            result["next_step"] = (
                "Widen date_from/date_to around the known filing day "
                "(avoid weekends/holidays if unsure)."
            )
        else:
            result["message"] = (
                "Filings existed on these file dates, but none matched the filters. "
                "Check company_name / sec_code / edinet_code / corporate_number and doc_types."
            )
            result["next_step"] = (
                "Prefer sec_code or edinet_code over a short company_name substring "
                "(partial name matches can include group companies)."
            )
    return _dump(result)


def _csv_cache_path(doc_id: str):
    """Private financials cache — not the save_document type=5 path (no TTL conflict)."""
    return cache.docs_dir() / "financials" / f"{doc_id}_type5.zip"


def _is_usable_csv_zip(raw: bytes) -> bool:
    try:
        save.assert_document_bytes(raw, 5)
        return True
    except EdinetError:
        return False


@_guard
def edinet_financials(args: dict) -> str:
    doc_id = save.normalize_doc_id(args.get("doc_id") or "")
    # csvFlag comes from a cached search list. 0 means this filing has no CSV.
    # That is not a missing document (not_found). Do not call EDINET for it.
    if cache.cached_csv_flag(doc_id) == "0":
        raise EdinetError(
            "This document has no CSV (csvFlag is 0). この書類には CSV が無い.",
            kind="no_csv",
            next_step=(
                "Pick a filing whose search row has csvFlag 1, "
                "or save the PDF with edinet_save_document type=2."
            ),
        )
    path = _csv_cache_path(doc_id)
    cached = False
    zip_bytes: Optional[bytes] = None

    if path.is_file() and path.stat().st_size > 0:
        age = time.time() - path.stat().st_mtime
        if age > cache.DOCS_CSV_TTL_SECONDS:
            with contextlib.suppress(OSError):
                path.unlink()
        else:
            candidate = path.read_bytes()
            if _is_usable_csv_zip(candidate):
                zip_bytes = candidate
                cached = True
            else:
                with contextlib.suppress(OSError):
                    path.unlink()

    if zip_bytes is None:
        # Guard before any EDINET GET so a denied/unavailable write path never hits the API.
        save.assert_path_writable(path)
        zip_bytes = client.fetch_document_bytes(doc_id, 5)
        save.assert_document_bytes(zip_bytes, 5)
        save.write_bytes_atomic(path, zip_bytes)
        cached = False

    extracted = metrics.extract_financials(zip_bytes, doc_id=doc_id)
    extracted["csv_cached"] = cached
    extracted["csv_path"] = str(path.resolve())
    extracted["csv_cache_ttl_seconds"] = cache.DOCS_CSV_TTL_SECONDS
    if path.is_file():
        extracted["csv_cached_mtime"] = datetime.fromtimestamp(
            path.stat().st_mtime, tz=JST
        ).isoformat()
    if cached:
        extracted["warnings"] = [
            "Using cached type=5 CSV ZIP within "
            f"{cache.DOCS_CSV_TTL_SECONDS // 3600}h TTL; not re-fetched from EDINET."
        ]
    return _dump(extracted)


@_guard
def edinet_save_document(args: dict) -> str:
    doc_id = (args.get("doc_id") or "").strip()
    doc_type = args.get("type", 2)
    try:
        doc_type = int(doc_type)
    except (TypeError, ValueError) as exc:
        raise EdinetError(
            "type must be an integer (1, 2, or 5).",
            kind="bad_request",
            next_step="Pass type=2 for PDF.",
        ) from exc
    return _dump(save.save_document(doc_id, doc_type))


HANDLERS = {
    "edinet_search_filings": edinet_search_filings,
    "edinet_financials": edinet_financials,
    "edinet_save_document": edinet_save_document,
}
