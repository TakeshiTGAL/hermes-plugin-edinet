"""Save EDINET document bytes into a Hermes-writable cache directory."""

from __future__ import annotations

import contextlib
import io
import os
import re
import tempfile
import zipfile
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

from . import cache, citation, client
from .errors import EdinetError

JST = ZoneInfo("Asia/Tokyo")
DOC_ID_RE = re.compile(r"^S[0-9A-Z]{7}$")
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")

# Declared (ZipInfo.file_size) and actual bytes read during decompress.
MAX_ZIP_MEMBER_UNCOMPRESSED = 50 * 1024 * 1024  # 50 MiB per member
MAX_ZIP_TOTAL_UNCOMPRESSED = 100 * 1024 * 1024  # 100 MiB across all members
_ZIP_READ_CHUNK = 1024 * 1024

TYPE_SUFFIX = {
    1: ".zip",
    2: ".pdf",
    5: ".zip",
}


def write_bytes_atomic(path: Path, data: bytes) -> None:
    """Write bytes via tempfile + os.replace (same pattern as list cache)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".doc-", suffix=path.suffix, dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _zip_member_name_unsafe(name: str) -> bool:
    """True if a ZIP member path could escape or is absolute (path traversal)."""
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:/", normalized):
        return True
    return any(part == ".." for part in normalized.split("/"))


def assert_zip_uncompressed_limits(zf: zipfile.ZipFile) -> None:
    """Refuse ZIP archives whose declared uncompressed sizes exceed caps (zip bomb)."""
    total = 0
    for info in zf.infolist():
        if _zip_member_name_unsafe(info.filename):
            raise EdinetError(
                "EDINET ZIP contains an unsafe member path "
                "(absolute path or '..'). Refusing to read.",
                kind="bad_response",
                next_step="Confirm the doc_id and type, then retry with another document.",
            )
        size = int(info.file_size)
        if size < 0 or size > MAX_ZIP_MEMBER_UNCOMPRESSED:
            raise EdinetError(
                "EDINET ZIP member exceeds the uncompressed size cap "
                f"({MAX_ZIP_MEMBER_UNCOMPRESSED // (1024 * 1024)} MiB). Refusing to read.",
                kind="bad_response",
                next_step="Confirm the doc_id and type, then retry with a smaller document.",
            )
        total += size
        if total > MAX_ZIP_TOTAL_UNCOMPRESSED:
            raise EdinetError(
                "EDINET ZIP total uncompressed size exceeds the cap "
                f"({MAX_ZIP_TOTAL_UNCOMPRESSED // (1024 * 1024)} MiB). Refusing to read.",
                kind="bad_response",
                next_step="Confirm the doc_id and type, then retry with a smaller document.",
            )


def read_zip_member_limited(
    zf: zipfile.ZipFile, name: str, *, total_so_far: int = 0
) -> tuple[bytes, int]:
    """Decompress one member with per-member and cumulative actual-byte caps."""
    chunks: list[bytes] = []
    read = 0
    with zf.open(name, "r") as handle:
        while True:
            chunk = handle.read(_ZIP_READ_CHUNK)
            if not chunk:
                break
            read += len(chunk)
            if read > MAX_ZIP_MEMBER_UNCOMPRESSED:
                raise EdinetError(
                    "EDINET ZIP member expanded past the uncompressed size cap "
                    f"({MAX_ZIP_MEMBER_UNCOMPRESSED // (1024 * 1024)} MiB). Refusing to read.",
                    kind="bad_response",
                    next_step="Confirm the doc_id and type, then retry with a smaller document.",
                )
            if total_so_far + read > MAX_ZIP_TOTAL_UNCOMPRESSED:
                raise EdinetError(
                    "EDINET ZIP total expanded past the uncompressed size cap "
                    f"({MAX_ZIP_TOTAL_UNCOMPRESSED // (1024 * 1024)} MiB). Refusing to read.",
                    kind="bad_response",
                    next_step="Confirm the doc_id and type, then retry with a smaller document.",
                )
            chunks.append(chunk)
    data = b"".join(chunks)
    return data, total_so_far + len(data)


def assert_zip_members_readable(zf: zipfile.ZipFile) -> None:
    """Check declared sizes, then stream-read every member under actual-byte caps."""
    assert_zip_uncompressed_limits(zf)
    total = 0
    for info in zf.infolist():
        _, total = read_zip_member_limited(zf, info.filename, total_so_far=total)


def normalize_doc_id(doc_id: str) -> str:
    """Validate EDINET docID shape and reject path characters.

    Letter case is normalized to uppercase (EDINET docIDs are case-insensitive in form).
    """
    raw = (doc_id or "").strip().upper()
    if not raw:
        raise EdinetError(
            "doc_id is required.",
            kind="bad_request",
            next_step="Pass a docID from edinet_search_filings (e.g. S100TR7I).",
        )
    if not DOC_ID_RE.fullmatch(raw):
        raise EdinetError(
            "doc_id must be an 8-character EDINET document id (e.g. S100TR7I).",
            kind="bad_request",
            next_step=(
                "EDINET document ids are 8 characters (for example S100TR7I). "
                "Pass one from edinet_search_filings."
            ),
        )
    safe = _SAFE.sub("-", raw).strip("-.")
    if safe != raw:
        raise EdinetError(
            "doc_id contains characters that are not allowed.",
            kind="bad_request",
            next_step="Confirm the doc_id from edinet_search_filings, then retry.",
        )
    return raw


def assert_document_bytes(raw: bytes, doc_type: int) -> None:
    """Reject JSON error envelopes and wrong magic bytes before any write."""
    if not raw:
        raise EdinetError(
            "EDINET returned an empty document body.",
            kind="bad_response",
            next_step="Confirm the doc_id and type, then retry.",
        )
    client._classify_edinet_body(raw)
    if doc_type == 2:
        if not raw.startswith(b"%PDF"):
            raise EdinetError(
                "EDINET body is not a PDF (%PDF magic missing). Refusing to save.",
                kind="bad_response",
                next_step="Confirm the doc_id and type=2, then retry.",
            )
        return
    if doc_type in (1, 5):
        if not raw.startswith(b"PK"):
            raise EdinetError(
                "EDINET body is not a ZIP (PK magic missing). Refusing to save.",
                kind="bad_response",
                next_step="Confirm the doc_id and type, then retry.",
            )
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                assert_zip_members_readable(zf)
        except zipfile.BadZipFile as exc:
            raise EdinetError(
                "EDINET body is not a valid ZIP archive. Refusing to save.",
                kind="bad_response",
                next_step="Confirm the doc_id and type, then retry.",
            ) from exc
        return
    raise EdinetError(
        f"Unsupported document type {doc_type}.",
        kind="bad_request",
        next_step="Pass type 1, 2, or 5.",
    )


def assert_path_writable(path: Path) -> None:
    """Ask Hermes write guard before creating/overwriting a file.

    Fail closed: if ``agent.file_safety`` cannot be imported, refuse to write.
    Raises EdinetError when the guard denies the path or is unavailable.
    """
    try:
        from agent.file_safety import get_write_denied_error
    except Exception as exc:
        raise EdinetError(
            "Hermes write guard (agent.file_safety) is unavailable; refusing to write.",
            kind="access_denied",
            next_step=(
                "Run inside Hermes Agent so agent.file_safety is importable, "
                "then retry. Cache is <HERMES_HOME>/plugin-data/jp-edinet for this profile, "
                "not a fallback under ~/.hermes."
            ),
        ) from exc
    try:
        message = get_write_denied_error(str(path))
    except Exception as exc:
        raise EdinetError(
            "Hermes write guard failed while checking the path; refusing to write.",
            kind="access_denied",
            next_step=(
                "Fix the Hermes write guard, then retry. "
                "Nothing is written and EDINET is not called when that check fails."
            ),
        ) from exc
    if message:
        raise EdinetError(
            message,
            kind="access_denied",
            next_step=(
                "Write under <HERMES_HOME>/plugin-data/jp-edinet for this profile, "
                "or adjust HERMES_WRITE_SAFE_ROOT."
            ),
        )


def save_document(doc_id: str, doc_type: int) -> dict:
    """Download type 1/2/5 and write under plugin-data/jp-edinet/docs. Returns absolute path."""
    if doc_type not in TYPE_SUFFIX:
        raise EdinetError(
            f"edinet_save_document supports type 1 (ZIP), 2 (PDF), or 5 (CSV ZIP); got {doc_type}.",
            kind="bad_request",
            next_step="Pass type=2 for PDF, type=1 for the submission ZIP, or type=5 for CSV.",
        )
    safe_id = normalize_doc_id(doc_id)
    directory = cache.docs_dir()
    filename = f"{safe_id}_type{doc_type}{TYPE_SUFFIX[doc_type]}"
    path = Path(directory) / filename
    # Guard before any EDINET GET so a denied/unavailable write path never hits the API.
    assert_path_writable(path)
    raw = client.fetch_document_bytes(safe_id, doc_type)
    assert_document_bytes(raw, doc_type)
    write_bytes_atomic(path, raw)
    mtime = path.stat().st_mtime
    return {
        "doc_id": safe_id,
        "type": doc_type,
        "path": str(path.resolve()),
        "bytes": len(raw),
        "saved_at": datetime.fromtimestamp(mtime, tz=JST).isoformat(),
        "note": (
            "File saved. This tool does not summarize document contents. "
            "saved_at is Japan time (JST), the same zone as EDINET file dates. "
            "No TTL; a later EDINET update for the same docID may leave this cache stale "
            "(check saved_at)."
        ),
        "source": {
            "citation": citation.edinet_citation(doc_id=safe_id),
            "excerpt_note": citation.excerpt_note(doc_id=safe_id),
            "filing_page": citation.filing_page_url(safe_id),
            "pdl": citation.PDL_URL,
        },
    }
