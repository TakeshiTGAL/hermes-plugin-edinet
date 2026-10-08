"""Tool schemas: first sentence must say what the tool does (~60 chars visible when deferred)."""

SEARCH_FILINGS = {
    "name": "edinet_search_filings",
    "description": (
        "Search EDINET filings by company and date range (有価証券報告書など). "
        "Filter by company_name, sec_code, edinet_code or corporate_number (法人番号), "
        "plus date_from/date_to (file dates) and optional doc_types "
        "(yuho / annual / quarterly / semiannual / extraordinary / amendment). "
        "Requires date_from/date_to plus at least one of company_name, sec_code, "
        "edinet_code, or corporate_number. The EDINET list API is by file date only; "
        "this tool iterates dates, caches each day's JSON under "
        "<HERMES_HOME>/plugin-data/jp-edinet/lists/<api-key-fingerprint>/ "
        "(per Hermes profile) with a short TTL "
        "(today JST ≤15 minutes, past dates ≤24 hours — past lists can still change), "
        "and filters locally. metadata.resultset.count must equal the row count or that "
        "day fails and is not cached. A 404 list (outside retention) is skipped "
        "(cache.days_list_unavailable). A 400 fails the whole date range. Ranges longer than max_calendar_days (default 31) return "
        "an error with next_step instead of truncating. When the row limit stops the "
        "date scan early, truncated=true and date_scan_stopped_at are set. Returns "
        "matches with docID, filing_page, flags and warnings, plus 出典/excerpt_note "
        "(same WZEK0030 shape as financials/save)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "company_name": {
                "type": "string",
                "description": (
                    "Substring match on filerName (e.g. 'トヨタ' or 'Sony'). "
                    "Short names can match group companies; prefer sec_code or "
                    "edinet_code when you need a single issuer."
                ),
            },
            "sec_code": {
                "type": "string",
                "description": "Securities code, 4 digits or EDINET's 5-digit form (7203 or 72030).",
            },
            "edinet_code": {
                "type": "string",
                "description": "EDINET issuer code, e.g. 'E02144'.",
            },
            "corporate_number": {
                "type": "string",
                "description": "13-digit corporate number (法人番号 / JCN).",
            },
            "date_from": {
                "type": "string",
                "description": "First file date (YYYY-MM-DD only), inclusive. Week and compact dates are rejected.",
            },
            "date_to": {
                "type": "string",
                "description": "Last file date (YYYY-MM-DD only), inclusive. Week and compact dates are rejected.",
            },
            "doc_types": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional document-kind filters: yuho/annual=120, quarterly=140, "
                    "semiannual=160, extraordinary=180/190, "
                    "amendment=130/150/170/190 (not 135 確認書 or 136 訂正確認書)."
                ),
            },
            "max_calendar_days": {
                "type": "integer",
                "description": (
                    "Optional tighter cap on inclusive calendar days. Effective limit is "
                    "min(arg, plugin config max_calendar_days); config itself is capped at "
                    "absolute maximum 31 (default 31). Args above the plugin cap are clamped "
                    "with a warning. Span over the effective limit → error."
                ),
            },
            "limit": {
                "type": "integer",
                "description": "Max rows to return (default 50). Integers outside 1–200 are rejected, not clamped.",
            },
        },
        "required": ["date_from", "date_to"],
        "anyOf": [
            {"required": ["company_name"]},
            {"required": ["sec_code"]},
            {"required": ["edinet_code"]},
            {"required": ["corporate_number"]},
        ],
    },
}

FINANCIALS = {
    "name": "edinet_financials",
    "description": (
        "Extract headline financials from an EDINET CSV ZIP. "
        "Pass doc_id from edinet_search_filings. Fetches type=5 CSV ZIP (or reuses a "
        "private financials cache for ≤24 hours, separate from edinet_save_document "
        "paths, then re-fetches; warns when cache is used), reads "
        "utf-16 TSV rows on CurrentYearDuration or CurrentYearInstant whose "
        "連結・個別 column is 連結 or その他 (member rows and 個別 are ignored), "
        "and returns revenue, "
        "operating_income, net_income, total_assets, net_assets/equity, "
        "operating_cash_flow with unit and accounting_standard (Japan GAAP / IFRS / "
        "US GAAP). revenue is the headline top-line CSV tag (売上高 / 営業収益 / Revenues); "
        "it is not always Japanese 売上高. net_assets/equity follow the matched tag "
        "(Japan GAAP NetAssetsSummary is usually 純資産合計 and may include NCI; "
        "IFRS/US GAAP preferred tags are usually equity attributable to owners — see "
        "metrics.*.meaning). When CSV label is blank, a short fallback from element_id "
        "is filled. Company-extension tags are matched by known aliases only "
        "(not exhaustive). Missing metrics are found: false — never invent 0. Cache "
        "writes check Hermes' writable-path guard before any type=5 GET. "
        "If a cached search list shows csvFlag 0, returns no_csv "
        "(this document has no CSV) and does not call EDINET. Includes "
        "出典 plus excerpt_note for EDINET."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "doc_id": {
                "type": "string",
                "description": "8-character EDINET document id, e.g. 'S100TR7I'.",
            },
        },
        "required": ["doc_id"],
    },
}

SAVE_DOCUMENT = {
    "name": "edinet_save_document",
    "description": (
        "Save an EDINET PDF or ZIP into Hermes cache. "
        "type=2 PDF, type=1 submission ZIP, type=5 CSV ZIP. Writes under "
        "<HERMES_HOME>/plugin-data/jp-edinet/docs/<api-key-fingerprint>/{docID}_typeN… "
        "(per Hermes profile) "
        "(no TTL; keyed by API-key fingerprint so another key cannot reuse the file). "
        "Hermes' writable-path guard runs before any document GET (refuses if the "
        "guard is unavailable or denies the path). "
        "Returns 出典/excerpt_note for the filing page. Does not summarize the file."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "doc_id": {
                "type": "string",
                "description": "8-character EDINET document id from edinet_search_filings.",
            },
            "type": {
                "type": "integer",
                "description": "Document type: 2=PDF (default), 1=ZIP, 5=CSV ZIP.",
            },
        },
        "required": ["doc_id"],
    },
}

ALL_SCHEMAS = [SEARCH_FILINGS, FINANCIALS, SAVE_DOCUMENT]
BY_NAME = {s["name"]: s for s in ALL_SCHEMAS}
