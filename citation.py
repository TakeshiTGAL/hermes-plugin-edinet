"""EDINET source-citation lines (利用規約 / WZEK0030)."""

from __future__ import annotations

PORTAL_URL = "https://disclosure2.edinet-fsa.go.jp/"
# PDL1.0 link target as cited on EDINET WZEK0030 (not the WZEK0030 page itself).
PDL_URL = "https://www.digital.go.jp/resources/open_data/public_data_license_v1.0"

# Viewing-site filing page used as「当該ページのURL」when a docID is known
# (WZEK0030 §1.1). Confirmed shape: WZEK0040.aspx?{docID}.
_FILING_PAGE = PORTAL_URL + "WZEK0040.aspx?{doc_id}"

# Search (no docID yet) cites the portal root plus PDL1.0 only.
CITATION_BASE = (
    f"出典：EDINET閲覧（提出）サイト（{PORTAL_URL}）、"
    f"PDL1.0（{PDL_URL}）"
)


def filing_page_url(doc_id: str) -> str:
    """Return the EDINET viewing-site page URL for a document id."""
    return _FILING_PAGE.format(doc_id=doc_id)


def excerpt_note(*, doc_id: str = "") -> str:
    """WZEK0030 excerpt line (separate from 出典). Prefer the filing page when known."""
    page = filing_page_url(doc_id) if doc_id else PORTAL_URL
    return f"EDINET閲覧（提出）サイト（{page}）より抜粋して作成"


# Backward-compatible constant for callers/tests that expect the portal form.
EXCERPT_NOTE = excerpt_note()


def edinet_citation(*, doc_id: str = "") -> str:
    """Return the 出典 line only. Never append the 抜粋 note.

    With doc_id: WZEK0030 §1.1「当該ページのURL」as the viewing-site filing page
    (``WZEK0040.aspx?{docID}``) plus PDL1.0. Without doc_id: portal root + PDL1.0.
    """
    if doc_id:
        page = filing_page_url(doc_id)
        return (
            f"出典：EDINET閲覧（提出）サイト（{page}）、"
            f"PDL1.0（{PDL_URL}）"
        )
    return CITATION_BASE
