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
# Headline totals for one fiscal year sit on that year's Duration / Instant only.
# Any longer context id is a member (segment, equity component, and so on).
# One yuho CSV carries the current year and up to four prior years (開示の「最近5連結会計年度」).
_PERIODS: tuple[tuple[str, str, str], ...] = (
    ("CurrentYear", "CurrentYearDuration", "CurrentYearInstant"),
    ("Prior1Year", "Prior1YearDuration", "Prior1YearInstant"),
    ("Prior2Year", "Prior2YearDuration", "Prior2YearInstant"),
    ("Prior3Year", "Prior3YearDuration", "Prior3YearInstant"),
    ("Prior4Year", "Prior4YearDuration", "Prior4YearInstant"),
)
_CONSOLIDATED_LABELS = frozenset({"連結", "その他"})


def _is_period_consolidated(
    context_id: str, consolidation: str, duration_id: str, instant_id: str
) -> bool:
    if context_id not in (duration_id, instant_id):
        return False
    return consolidation in _CONSOLIDATED_LABELS


def _is_period_filing_company(
    context_id: str, consolidation: str, duration_id: str, instant_id: str
) -> bool:
    """Filing-company headline only. A further member under NonConsolidatedMember is not the total."""
    if consolidation != "個別":
        return False
    return context_id in (
        f"{duration_id}_NonConsolidatedMember",
        f"{instant_id}_NonConsolidatedMember",
    )


def _in_scope(
    scope: str,
    context_id: str,
    consolidation: str,
    duration_id: str,
    instant_id: str,
) -> bool:
    if scope == "filing_company":
        return _is_period_filing_company(context_id, consolidation, duration_id, instant_id)
    return _is_period_consolidated(context_id, consolidation, duration_id, instant_id)


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
                        "relative_year": (row.get("相対年度") or "").strip(),
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


def _standard_penalty(element_id: str, standard: Optional[str]) -> int:
    """Keep one filing on one standard's tags when both appear in the five-year table."""
    if not standard:
        return 0
    upper = element_id.upper()
    if standard == "IFRS" and "USGAAP" in upper:
        return 800
    if standard == "US GAAP" and "IFRS" in upper:
        return 800
    return 0


def _score_candidate(
    metric: str,
    element_id: str,
    context_id: str,
    consolidation: str,
    duration_id: str,
    instant_id: str,
    standard: Optional[str] = None,
    scope: str = "consolidated",
) -> int:
    """Higher is better. Exact preferred IDs beat contains-matches."""
    if any(s in element_id for s in SKIP_SUBSTRINGS):
        return -1
    if not _in_scope(scope, context_id, consolidation, duration_id, instant_id):
        return -1
    preferred = METRIC_ELEMENT_IDS.get(metric, [])
    if element_id in preferred:
        # Summary rows beat statement body duplicates.
        base = 1000 - preferred.index(element_id)
        if "SummaryOfBusinessResults" in element_id:
            base += 50
        if context_id in (duration_id, instant_id):
            base += 10
        return base - _standard_penalty(element_id, standard)
    for i, needle in enumerate(METRIC_CONTAINS.get(metric, [])):
        if needle in element_id:
            base = 500 - i
            if "SummaryOfBusinessResults" in element_id or "KeyFinancialData" in element_id:
                base += 40
            if context_id in (duration_id, instant_id):
                base += 10
            return base - _standard_penalty(element_id, standard)
    # 当期純利益 on the filing-company statement. Consolidated aliases name
    # owners-of-parent tags and do not match this id. Score stays under those
    # aliases so the consolidated series is unchanged.
    if (
        scope == "filing_company"
        and metric == "net_income"
        and element_id == "jppfs_cor:ProfitLoss"
    ):
        return 400
    return -1


def _missing_metric(metric: str, period_id: str, scope: str = "consolidated") -> dict[str, Any]:
    series = "consolidated" if scope == "consolidated" else "filing-company"
    if metric == "operating_income" and scope == "consolidated":
        return {
            "found": False,
            "value": None,
            "unit": None,
            "element_id": None,
            "context_id": None,
            "relative_year": None,
            "label": None,
            "message": (
                f"No consolidated {period_id} operating-income value matched this "
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
        "relative_year": None,
        "label": None,
        "message": (
            f"No {period_id} {series} '{metric}' matched this plugin's known "
            "element_id aliases (company-extension tags are not exhaustive); "
            "do not invent 0."
        ),
        "next_step": (
            "Call `edinet_save_document` with type=2 (PDF) and read the figure "
            "from the filing, or retry after a plugin update that adds the tag."
        ),
    }


def find_metric(
    rows: list[dict[str, str]],
    metric: str,
    period_id: str = "CurrentYear",
    duration_id: str = "CurrentYearDuration",
    instant_id: str = "CurrentYearInstant",
    standard: Optional[str] = None,
    scope: str = "consolidated",
) -> dict[str, Any]:
    best: Optional[tuple[int, dict[str, str]]] = None
    for row in rows:
        score = _score_candidate(
            metric,
            row["element_id"],
            row["context_id"],
            row.get("consolidation") or "",
            duration_id,
            instant_id,
            standard,
            scope,
        )
        if score < 0:
            continue
        number = _parse_number(row["value"])
        if number is None:
            continue
        if best is None or score > best[0]:
            best = (score, row)
    if best is None:
        return _missing_metric(metric, period_id, scope)
    row = best[1]
    label = row["label"] or _fallback_label(row["element_id"])
    return {
        "found": True,
        "value": _parse_number(row["value"]),
        "unit": row["unit"] or None,
        "element_id": row["element_id"],
        "context_id": row["context_id"],
        "relative_year": row.get("relative_year") or None,
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


def _period_block(
    rows: list[dict[str, str]],
    period_id: str,
    duration_id: str,
    instant_id: str,
    standard: Optional[str],
    scope: str = "consolidated",
) -> dict[str, Any]:
    metrics = {
        name: find_metric(rows, name, period_id, duration_id, instant_id, standard, scope)
        for name in METRIC_ELEMENT_IDS
    }
    metrics["equity"] = dict(metrics["net_assets"])
    meaning = _net_assets_meaning(metrics["net_assets"].get("element_id"))
    for name in ("net_assets", "equity"):
        metrics[name]["meaning"] = meaning
    relative = None
    for item in metrics.values():
        if item.get("relative_year"):
            relative = item["relative_year"]
            break
    found_count = sum(1 for name in METRIC_ELEMENT_IDS if metrics[name].get("found"))
    return {
        "period_id": period_id,
        "scope": "consolidated" if scope == "consolidated" else "non_consolidated",
        "relative_year": relative,
        "found_lines": found_count,
        "metrics": metrics,
    }


def _series_note(periods: list[dict[str, Any]], filing_company: list[dict[str, Any]]) -> str:
    note = (
        "Two series from one EDINET API v2 CSV (type=5). "
        "periods is consolidated: that year's Duration or Instant whose "
        "連結・個別 column is 連結 or その他 (CurrentYear plus Prior1Year "
        "through Prior4Year, at most five). "
        "filing_company is the filing company only: the same year's context "
        "whose id is exactly Duration or Instant plus _NonConsolidatedMember, "
        "連結・個別 個別, and no further member. "
        "A missing line stays found=false and value=null. "
        "It is not copied from the other series and it is not written as 0. "
        "Do not add the two amounts. "
        "net_assets/equity meaning depends on the matched tag (see metrics.*.meaning). "
        "Copy each line's unit string when drawing a chart."
    )
    consolidated = periods[0]["metrics"]["revenue"] if periods else {}
    filing = filing_company[0]["metrics"]["revenue"] if filing_company else {}
    if consolidated.get("found") and filing.get("found") and consolidated.get("value") != filing.get("value"):
        note += (
            f" CurrentYear consolidated revenue is {consolidated.get('value')} "
            f"{consolidated.get('unit') or ''}. "
            f"CurrentYear filing-company revenue is {filing.get('value')} "
            f"{filing.get('unit') or ''}. "
            "These amounts are different statements. Do not add them and do not copy one into the other."
        )
    return note


def extract_financials(zip_bytes: bytes, *, doc_id: str = "") -> dict[str, Any]:
    rows = iter_csv_rows(zip_bytes)
    standard = accounting_standard(rows)
    periods = [
        _period_block(rows, period_id, duration_id, instant_id, standard, "consolidated")
        for period_id, duration_id, instant_id in _PERIODS
    ]
    filing_company = [
        _period_block(rows, period_id, duration_id, instant_id, standard, "filing_company")
        for period_id, duration_id, instant_id in _PERIODS
    ]
    # `metrics` stays the current-year consolidated block (callers written before periods).
    metrics = periods[0]["metrics"]
    return {
        "doc_id": doc_id or None,
        "scope": "consolidated",
        "accounting_standard": standard,
        "metrics": metrics,
        "periods": periods,
        "filing_company": filing_company,
        "source": {
            "citation": citation.edinet_citation(doc_id=doc_id),
            "excerpt_note": citation.excerpt_note(doc_id=doc_id),
            "portal": citation.PORTAL_URL,
            "filing_page": citation.filing_page_url(doc_id) if doc_id else None,
            "pdl": citation.PDL_URL,
            "note": _series_note(periods, filing_company),
        },
    }
