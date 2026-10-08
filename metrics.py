"""Extract headline financial metrics from an EDINET type=5 CSV ZIP (utf-16 TSV)."""

from __future__ import annotations

import csv
import io
import zipfile
from typing import Any, Optional

from . import citation, save

# Preferred CurrentYear* element IDs, ordered most-specific first.
# Never invent a zero: if nothing matches, the metric is missing.
METRIC_ELEMENT_IDS: dict[str, list[str]] = {
    "revenue": [
        "jpcrp_cor:NetSalesSummaryOfBusinessResults",
        "jpcrp_cor:RevenuesUSGAAPSummaryOfBusinessResults",
        "jpcrp_cor:RevenueIFRSSummaryOfBusinessResults",
        "jppfs_cor:NetSales",
        "jpigp_cor:RevenueIFRS",
    ],
    "operating_income": [
        "jpcrp_cor:OperatingIncomeSummaryOfBusinessResults",
        "jpcrp_cor:OperatingIncomeUSGAAPSummaryOfBusinessResults",
        "jpcrp_cor:OperatingProfitLossIFRSSummaryOfBusinessResults",
        "jppfs_cor:OperatingIncome",
        "jpigp_cor:OperatingProfitLossIFRS",
    ],
    "net_income": [
        "jpcrp_cor:ProfitLossAttributableToOwnersOfParentSummaryOfBusinessResults",
        "jpcrp_cor:ProfitLossAttributableToOwnersOfParentIFRSSummaryOfBusinessResults",
        "jpcrp_cor:NetIncomeLossAttributableToOwnersOfParentUSGAAPSummaryOfBusinessResults",
        "jpigp_cor:ProfitLossAttributableToOwnersOfParentIFRS",
        "jppfs_cor:ProfitLossAttributableToOwnersOfParent",
        "jpcrp_cor:NetIncomeLossSummaryOfBusinessResults",
    ],
    "total_assets": [
        "jpcrp_cor:TotalAssetsSummaryOfBusinessResults",
        "jpcrp_cor:TotalAssetsIFRSSummaryOfBusinessResults",
        "jpcrp_cor:TotalAssetsUSGAAPSummaryOfBusinessResults",
        "jppfs_cor:Assets",
        "jpigp_cor:AssetsIFRS",
    ],
    "net_assets": [
        "jpcrp_cor:NetAssetsSummaryOfBusinessResults",
        "jpcrp_cor:EquityAttributableToOwnersOfParentIFRSSummaryOfBusinessResults",
        "jpcrp_cor:EquityAttributableToOwnersOfParentUSGAAPSummaryOfBusinessResults",
        "jpigp_cor:EquityAttributableToOwnersOfParentIFRS",
        "jppfs_cor:NetAssets",
    ],
    "operating_cash_flow": [
        "jpcrp_cor:CashFlowsFromUsedInOperatingActivitiesSummaryOfBusinessResults",
        "jpcrp_cor:CashFlowsFromUsedInOperatingActivitiesIFRSSummaryOfBusinessResults",
        "jpcrp_cor:CashFlowsFromUsedInOperatingActivitiesUSGAAPSummaryOfBusinessResults",
        "jpcrp_cor:NetCashProvidedByUsedInOperatingActivitiesSummaryOfBusinessResults",
        "jppfs_cor:NetCashProvidedByUsedInOperatingActivities",
        "jpigp_cor:CashFlowsFromUsedInOperatingActivitiesIFRS",
    ],
}

# Suffix / contains patterns used when company-specific taxonomy IDs appear (Toyota IFRS).
METRIC_CONTAINS: dict[str, list[str]] = {
    "revenue": [
        # Prefer KeyFinancialData headlines (e.g. Sony 売上高及び金融ビジネス収入)
        # over lower jpigp NetSalesIFRS statement rows when both exist.
        "SalesAndFinancialServicesRevenueIFRSKeyFinancialData",
        "OperatingRevenuesIFRSKeyFinancialData",
        "TotalNetRevenuesIFRS",
        "SalesRevenuesIFRS",
        "SalesAndFinancialServicesRevenue",
        "NetSalesSummaryOfBusinessResults",
        "RevenuesUSGAAPSummaryOfBusinessResults",
    ],
    "operating_income": [
        "OperatingIncomeSummaryOfBusinessResults",
        "OperatingIncomeUSGAAP",
        "OperatingProfitLossIFRSSummaryOfBusinessResults",
        "OperatingProfitLossIFRS",
    ],
    "net_income": [
        "ProfitLossAttributableToOwnersOfParentIFRSSummaryOfBusinessResults",
        "NetIncomeLossAttributableToOwnersOfParentUSGAAPSummaryOfBusinessResults",
        "ProfitLossAttributableToOwnersOfParentSummaryOfBusinessResults",
    ],
    "total_assets": [
        "TotalAssetsIFRSSummaryOfBusinessResults",
        "TotalAssetsUSGAAPSummaryOfBusinessResults",
        "TotalAssetsSummaryOfBusinessResults",
    ],
    "net_assets": [
        "EquityAttributableToOwnersOfParentIFRSSummaryOfBusinessResults",
        "EquityAttributableToOwnersOfParentUSGAAPSummaryOfBusinessResults",
        "NetAssetsSummaryOfBusinessResults",
    ],
    "operating_cash_flow": [
        "CashFlowsFromUsedInOperatingActivitiesIFRSSummaryOfBusinessResults",
        "CashFlowsFromUsedInOperatingActivitiesUSGAAPSummaryOfBusinessResults",
        "NetCashProvidedByUsedInOperatingActivitiesSummaryOfBusinessResults",
        "CashFlowsFromUsedInOperatingActivitiesSummaryOfBusinessResults",
    ],
}

ACCOUNTING_STANDARD_ID = "jpdei_cor:AccountingStandardsDEI"

SKIP_SUBSTRINGS = (
    "TextBlock",
    "PerShare",
    "Ratio",
    "RateOfReturn",
    "NumberOf",
    "BookValue",
    "DetailsOf",
    "Notes",
    "Comprehensive",
    "NonOperating",
    "Intersegment",
    "FromExternalCustomers",
)

# When CSV 項目名 is blank (common on company-extension tags), fill a short display label.
_LABEL_HINTS: tuple[tuple[str, str], ...] = (
    ("OperatingRevenuesIFRSKeyFinancialData", "営業収益（IFRS）"),
    ("OperatingRevenuesIFRS", "営業収益（IFRS）"),
    ("TotalNetRevenuesIFRS", "売上高合計（IFRS）"),
    ("SalesAndFinancialServicesRevenue", "売上高及び金融ビジネス収入"),
    ("NetSalesSummaryOfBusinessResults", "売上高、経営指標等"),
    ("RevenuesUSGAAPSummaryOfBusinessResults", "売上高、経営指標等（US GAAP）"),
    ("RevenueIFRSSummaryOfBusinessResults", "売上高、経営指標等（IFRS）"),
    ("OperatingIncomeSummaryOfBusinessResults", "営業利益、経営指標等"),
    ("OperatingProfitLossIFRSSummaryOfBusinessResults", "営業利益（損失）（IFRS）、経営指標等"),
    ("TotalAssetsSummaryOfBusinessResults", "総資産額、経営指標等"),
    ("TotalAssetsIFRSSummaryOfBusinessResults", "総資産額、経営指標等（IFRS）"),
    ("NetAssetsSummaryOfBusinessResults", "純資産額、経営指標等"),
    ("EquityAttributableToOwnersOfParentIFRSSummaryOfBusinessResults", "親会社の所有者に帰属する持分（IFRS）"),
    ("EquityAttributableToOwnersOfParentUSGAAPSummaryOfBusinessResults", "親会社の所有者に帰属する持分（US GAAP）"),
)


def _fallback_label(element_id: str) -> Optional[str]:
    local = element_id.rsplit(":", 1)[-1]
    for needle, label in _LABEL_HINTS:
        if needle in local:
            return label
    return None


# EDINET's CSV labels a context that has no consolidation-axis member as その他.
# 連結 is the explicit consolidated label. 個別 is non-consolidated.
# Headline totals sit on CurrentYearDuration / CurrentYearInstant only.
# Any longer context id is a member (segment, equity component, and so on).
_NO_MEMBER_CONTEXTS = frozenset({"CurrentYearDuration", "CurrentYearInstant"})
_CONSOLIDATED_LABELS = frozenset({"連結", "その他"})


def _is_current_year_consolidated(context_id: str, consolidation: str) -> bool:
    if context_id not in _NO_MEMBER_CONTEXTS:
        return False
    return consolidation in _CONSOLIDATED_LABELS


def _parse_number(raw: str) -> Optional[int | float]:
    text = (raw or "").strip().replace(",", "")
    if not text or text in ("－", "-", "—", "―", "N/A", "n/a"):
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    # EDINET yen figures are whole units; prefer JSON integers over floats.
    as_int = int(round(number))
    if abs(number - as_int) < 1e-6:
        return as_int
    return number


def iter_csv_rows(zip_bytes: bytes) -> list[dict[str, str]]:
    """Read utf-16 TSV rows from every .csv member of the type=5 ZIP."""
    rows: list[dict[str, str]] = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        save.assert_zip_uncompressed_limits(zf)
        names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        # Prefer the company report CSV (jpcrp) first — it holds SummaryOfBusinessResults.
        names.sort(key=lambda n: (0 if "jpcrp" in n else 1, n))
        total_read = 0
        for name in names:
            raw, total_read = save.read_zip_member_limited(
                zf, name, total_so_far=total_read
            )
            text = raw.decode("utf-16")
            reader = csv.DictReader(io.StringIO(text), delimiter="\t")
            for row in reader:
                rows.append(
                    {
                        "element_id": (row.get("要素ID") or "").strip(),
                        "label": (row.get("項目名") or "").strip(),
                        "context_id": (row.get("コンテキストID") or "").strip(),
                        "consolidation": (row.get("連結・個別") or "").strip(),
                        "unit": (row.get("単位") or "").strip(),
                        "value": (row.get("値") or "").strip(),
                        "member_file": name,
                    }
                )
    return rows


def accounting_standard(rows: list[dict[str, str]]) -> Optional[str]:
    for row in rows:
        if row["element_id"] == ACCOUNTING_STANDARD_ID and row["value"]:
            return row["value"].strip()
    return None


def _score_candidate(
    metric: str, element_id: str, context_id: str, consolidation: str
) -> int:
    """Higher is better. Exact preferred IDs beat contains-matches."""
    if any(s in element_id for s in SKIP_SUBSTRINGS):
        return -1
    if not _is_current_year_consolidated(context_id, consolidation):
        return -1
    preferred = METRIC_ELEMENT_IDS.get(metric, [])
    if element_id in preferred:
        # Summary rows beat statement body duplicates.
        base = 1000 - preferred.index(element_id)
        if "SummaryOfBusinessResults" in element_id:
            base += 50
        if context_id in ("CurrentYearDuration", "CurrentYearInstant"):
            base += 10
        return base
    for i, needle in enumerate(METRIC_CONTAINS.get(metric, [])):
        if needle in element_id:
            base = 500 - i
            if "SummaryOfBusinessResults" in element_id or "KeyFinancialData" in element_id:
                base += 40
            if context_id in ("CurrentYearDuration", "CurrentYearInstant"):
                base += 10
            return base
    return -1


def find_metric(rows: list[dict[str, str]], metric: str) -> dict[str, Any]:
    best: Optional[tuple[int, dict[str, str]]] = None
    for row in rows:
        score = _score_candidate(
            metric,
            row["element_id"],
            row["context_id"],
            row.get("consolidation") or "",
        )
        if score < 0:
            continue
        number = _parse_number(row["value"])
        if number is None:
            continue
        if best is None or score > best[0]:
            best = (score, row)
    if best is None:
        if metric == "operating_income":
            return {
                "found": False,
                "value": None,
                "unit": None,
                "element_id": None,
                "context_id": None,
                "label": None,
                "message": (
                    "No consolidated CurrentYear operating-income value matched this "
                    "plugin's known aliases (NonConsolidated and 個別 rows, and context "
                    "members such as segments or equity components, are ignored; some "
                    "US GAAP filers put consolidated OI only in a PDF/text block)."
                ),
                "next_step": (
                    "Call `edinet_save_document` with type=2 (PDF) and check the "
                    "consolidated income statement."
                ),
            }
        return {
            "found": False,
            "value": None,
            "unit": None,
            "element_id": None,
            "context_id": None,
            "label": None,
            "message": (
                f"No CurrentYear consolidated '{metric}' matched this plugin's known "
                "element_id aliases (company-extension tags are not exhaustive); "
                "do not invent 0."
            ),
            "next_step": (
                "Call `edinet_save_document` with type=2 (PDF) and read the figure "
                "from the filing, or retry after a plugin update that adds the tag."
            ),
        }
    row = best[1]
    label = row["label"] or _fallback_label(row["element_id"])
    return {
        "found": True,
        "value": _parse_number(row["value"]),
        "unit": row["unit"] or None,
        "element_id": row["element_id"],
        "context_id": row["context_id"],
        "label": label,
        "message": None,
    }


def _net_assets_meaning(element_id: Optional[str]) -> str:
    """Describe what net_assets/equity means for the matched taxonomy tag."""
    eid = element_id or ""
    if "EquityAttributableToOwnersOfParent" in eid:
        return (
            "owners_equity (親会社の持分 / equity attributable to owners of parent); "
            "not 純資産合計 including non-controlling interests"
        )
    if "NetAssets" in eid:
        return (
            "total_net_assets (経営指標等の純資産額 / 純資産合計; may include "
            "non-controlling interests)"
        )
    return (
        "summary-row equity/net assets from the matched element_id; check label "
        "and element_id (Japan GAAP NetAssetsSummary is often 純資産合計; IFRS/US "
        "GAAP preferred tags are often equity attributable to owners)"
    )


def extract_financials(zip_bytes: bytes, *, doc_id: str = "") -> dict[str, Any]:
    rows = iter_csv_rows(zip_bytes)
    standard = accounting_standard(rows)
    metrics = {name: find_metric(rows, name) for name in METRIC_ELEMENT_IDS}
    # Alias equity -> same as net_assets for callers who ask for equity.
    metrics["equity"] = dict(metrics["net_assets"])
    meaning = _net_assets_meaning(metrics["net_assets"].get("element_id"))
    for name in ("net_assets", "equity"):
        metrics[name]["meaning"] = meaning
    return {
        "doc_id": doc_id or None,
        "accounting_standard": standard,
        "metrics": metrics,
        "source": {
            "citation": citation.edinet_citation(doc_id=doc_id),
            "excerpt_note": citation.excerpt_note(doc_id=doc_id),
            "portal": citation.PORTAL_URL,
            "filing_page": citation.filing_page_url(doc_id) if doc_id else None,
            "pdl": citation.PDL_URL,
            "note": (
                "Figures come from EDINET API v2 CSV (type=5) rows on "
                "CurrentYearDuration or CurrentYearInstant whose 連結・個別 column "
                "is 連結 or その他. 個別 rows and member contexts are ignored. "
                "Missing metrics are reported as found=false; values are never invented. "
                "net_assets/equity meaning depends on the matched tag (see metrics.*.meaning)."
            ),
        },
    }
