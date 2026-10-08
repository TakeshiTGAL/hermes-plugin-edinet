"""Offline tests for jp-edinet registration, search, financials, save, and scrubbing."""

from __future__ import annotations

import json
import os
import time
from datetime import date
from pathlib import Path

import pytest
import yaml

PLUGIN_DIR = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
FAKE_API_KEY = "test-edinet-key-DO-NOT-USE-live-0000"
CSV_BY_DOC = {
    "S100TR7I": "csv_toyota_ifrs_S100TR7I.zip",
    "S100TMMG": "csv_nintendo_jpgaap_S100TMMG.zip",
    "S100T58N": "csv_canon_usgaap_S100T58N.zip",
}
TOOL_NAMES = ["edinet_search_filings", "edinet_financials", "edinet_save_document"]


def _current_year_value(zip_name: str, element_id: str) -> float:
    """Read a CurrentYear consolidated value directly from a fixture ZIP (test oracle)."""
    import csv
    import io
    import zipfile

    raw = (FIXTURES / zip_name).read_bytes()
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        for name in zf.namelist():
            if not name.endswith(".csv"):
                continue
            text = zf.read(name).decode("utf-16")
            for row in csv.DictReader(io.StringIO(text), delimiter="\t"):
                eid = (row.get("要素ID") or "").strip()
                ctx = (row.get("コンテキストID") or "").strip()
                col = (row.get("連結・個別") or "").strip()
                if eid != element_id:
                    continue
                if ctx not in ("CurrentYearDuration", "CurrentYearInstant"):
                    continue
                if col not in ("連結", "その他"):
                    continue
                return float((row.get("値") or "").replace(",", ""))
    raise AssertionError(f"element {element_id} not found in {zip_name}")


# --- registration / manifest ----------------------------------------------------


def test_register_wires_three_tools(plugin):
    registered = {}

    class Ctx:
        def register_tool(self, name, toolset, schema, handler, **kwargs):
            registered[name] = (toolset, schema, handler, kwargs)

    plugin.register(Ctx())
    assert list(registered) == TOOL_NAMES
    for name, (toolset, schema, handler, kwargs) in registered.items():
        assert toolset == "edinet"
        assert schema["name"] == name
        assert callable(handler)
        assert kwargs["requires_env"] == ["EDINET_API_KEY"]


def test_manifest_matches_registration(plugin):
    manifest = yaml.safe_load((PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8"))
    registered = {}

    class Ctx:
        def register_tool(self, name, toolset, schema, handler, **kwargs):
            registered[name] = kwargs["requires_env"]

    plugin.register(Ctx())
    assert manifest["name"] == "jp-edinet"
    assert manifest["manifest_version"] == 2
    assert manifest["requires_hermes"] == ">=0.21.4"
    assert manifest["provides_tools"] == list(registered)
    assert [e["name"] for e in manifest["requires_env"]] == ["EDINET_API_KEY"]
    assert all(e.get("secret") for e in manifest["requires_env"])
    assert plugin.client.USER_AGENT.endswith("/" + manifest["version"])


def test_schemas_first_sentence_says_what_tool_does(plugin):
    for schema in plugin.schemas.ALL_SCHEMAS:
        first = schema["description"].split(".")[0]
        assert len(first) >= 20
        assert len(first) <= 120
        params = schema["parameters"]
        assert params["type"] == "object"
        assert set(params["required"]) <= set(params["properties"])


def test_no_unofficial_wording_in_readme_and_manifest():
    text = (PLUGIN_DIR / "README.md").read_text(encoding="utf-8")
    manifest = (PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8")
    for banned in ("Unofficial", "非公式", "not affiliated", "Not affiliated"):
        assert banned not in text
        assert banned not in manifest
    assert "built on the EDINET API" in text or "EDINET を活用" in text


# --- search ---------------------------------------------------------------------


def test_search_by_sec_code_7203(run):
    out = run(
        "edinet_search_filings",
        {
            "sec_code": "7203",
            "date_from": "2024-06-25",
            "date_to": "2024-06-25",
            "doc_types": ["yuho"],
        },
    )
    assert out["count"] >= 1
    ids = {f["docID"] for f in out["filings"]}
    assert "S100TR7I" in ids
    row = next(f for f in out["filings"] if f["docID"] == "S100TR7I")
    assert row["filerName"] == "トヨタ自動車株式会社"
    assert row["edinetCode"] == "E02144"
    assert row["csvFlag"] == "1"
    assert "出典：EDINET" in out["source"]["citation"]


def test_search_by_company_name_toyota(run):
    out = run(
        "edinet_search_filings",
        {
            "company_name": "トヨタ",
            "date_from": "2024-06-25",
            "date_to": "2024-06-25",
        },
    )
    assert any(f["docID"] == "S100TR7I" for f in out["filings"])
    assert any("substring" in w.lower() for w in out.get("warnings", []))
    assert "excerpt_note" in out["source"]
    assert "より抜粋して作成" in out["source"]["excerpt_note"]
    if out["count"] == 1:
        assert "WZEK0040.aspx?S100TR7I" in out["source"]["excerpt_note"]
    else:
        assert "WZEK0040.aspx?" not in out["source"]["excerpt_note"]


def test_search_by_jcn(run):
    out = run(
        "edinet_search_filings",
        {
            "corporate_number": "1180301018771",
            "date_from": "2024-06-25",
            "date_to": "2024-06-25",
        },
    )
    assert out["filings"][0]["docID"] == "S100TR7I"
    assert out["filings"][0]["JCN"] == "1180301018771"


def test_search_by_edinet_code(run):
    out = run(
        "edinet_search_filings",
        {
            "edinet_code": "E02367",
            "date_from": "2024-06-28",
            "date_to": "2024-06-28",
        },
    )
    assert out["filings"][0]["docID"] == "S100TMMG"
    assert "任天堂" in out["filings"][0]["filerName"]


def test_search_sony_on_same_day(run):
    out = run(
        "edinet_search_filings",
        {
            "sec_code": "6758",
            "date_from": "2024-06-25",
            "date_to": "2024-06-25",
            "doc_types": ["yuho"],
        },
    )
    assert any(f["docID"] == "S100TS7P" for f in out["filings"])


def test_search_range_over_max_errors_with_next_step(run):
    out = run(
        "edinet_search_filings",
        {
            "sec_code": "7203",
            "date_from": "2024-01-01",
            "date_to": "2024-03-01",
            "max_calendar_days": 31,
        },
    )
    assert "error" in out
    assert out["kind"] == "range_too_large"
    assert "next_step" in out
    assert "not truncated" in out["error"].lower() or "was not truncated" in out["error"]
    assert out["calendar_days"] > 31


def test_search_requires_identity(run):
    out = run(
        "edinet_search_filings",
        {"date_from": "2024-06-25", "date_to": "2024-06-25"},
    )
    assert out["kind"] == "bad_request"
    assert "next_step" in out


def test_search_uses_disk_cache_on_second_call(run, plugin, api, _isolate_hermes_home):
    args = {
        "sec_code": "7203",
        "date_from": "2024-06-25",
        "date_to": "2024-06-25",
    }
    first = run("edinet_search_filings", args)
    assert first["cache"]["days_fetched"] == 1
    list_calls = [c for c in api if c[0] == "list"]
    n_before = len(list_calls)
    second = run("edinet_search_filings", args)
    assert second["cache"]["days_cache_hit"] == 1
    assert len([c for c in api if c[0] == "list"]) == n_before  # no new fetch


def test_missing_api_key_gives_next_step(plugin, monkeypatch, _isolate_hermes_home):
    monkeypatch.delenv("EDINET_API_KEY", raising=False)
    # Keep the real fetch_documents_list so it checks the env; block the socket.
    monkeypatch.setattr(
        plugin.client,
        "_http_get_bytes",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not hit the network")),
    )
    raw = plugin.tools.HANDLERS["edinet_search_filings"](
        {
            "sec_code": "7203",
            "date_from": "2024-06-25",
            "date_to": "2024-06-25",
        }
    )
    out = json.loads(raw)
    assert out["kind"] == "api_key_missing_or_invalid"
    assert "next_step" in out
    assert "EDINET_API_KEY" in out["error"] or "EDINET_API_KEY" in out["next_step"]
    assert "WEEK0060" in out["next_step"]
    assert FAKE_API_KEY not in raw
    assert "Subscription-Key" not in raw


# --- financials -----------------------------------------------------------------


@pytest.mark.parametrize(
    "doc_id,zip_name,standard,revenue_id,assets_id",
    [
        (
            "S100TR7I",
            "csv_toyota_ifrs_S100TR7I.zip",
            "IFRS",
            "jpcrp030000-asr_E02144-000:OperatingRevenuesIFRSKeyFinancialData",
            "jpcrp_cor:TotalAssetsIFRSSummaryOfBusinessResults",
        ),
        (
            "S100TMMG",
            "csv_nintendo_jpgaap_S100TMMG.zip",
            "Japan GAAP",
            "jpcrp_cor:NetSalesSummaryOfBusinessResults",
            "jpcrp_cor:TotalAssetsSummaryOfBusinessResults",
        ),
        (
            "S100T58N",
            "csv_canon_usgaap_S100T58N.zip",
            "US GAAP",
            "jpcrp_cor:RevenuesUSGAAPSummaryOfBusinessResults",
            "jpcrp_cor:TotalAssetsUSGAAPSummaryOfBusinessResults",
        ),
    ],
)
def test_financials_three_standards(run, doc_id, zip_name, standard, revenue_id, assets_id):
    out = run("edinet_financials", {"doc_id": doc_id})
    assert out["accounting_standard"] == standard
    rev = out["metrics"]["revenue"]
    assets = out["metrics"]["total_assets"]
    assert rev["found"] is True
    assert assets["found"] is True
    assert rev["unit"] == "円"
    assert assets["unit"] == "円"
    assert rev["value"] == _current_year_value(zip_name, revenue_id)
    assert assets["value"] == _current_year_value(zip_name, assets_id)
    assert "出典：EDINET" in out["source"]["citation"]
    assert doc_id in out["source"]["citation"]


def test_financials_toyota_operating_income_and_ocf(run):
    out = run("edinet_financials", {"doc_id": "S100TR7I"})
    assert out["metrics"]["operating_income"]["found"] is True
    assert out["metrics"]["operating_cash_flow"]["found"] is True
    assert out["metrics"]["net_income"]["value"] == _current_year_value(
        "csv_toyota_ifrs_S100TR7I.zip",
        "jpcrp_cor:ProfitLossAttributableToOwnersOfParentIFRSSummaryOfBusinessResults",
    )


def test_financials_nintendo_japan_gaap_operating_income(run):
    out = run("edinet_financials", {"doc_id": "S100TMMG"})
    assert out["accounting_standard"] == "Japan GAAP"
    assert out["metrics"]["operating_income"]["value"] == 528941000000.0
    assert out["metrics"]["operating_cash_flow"]["value"] == 462097000000.0


def test_financials_canon_missing_operating_income(run):
    out = run("edinet_financials", {"doc_id": "S100T58N"})
    assert out["accounting_standard"] == "US GAAP"
    oi = out["metrics"]["operating_income"]
    assert oi["found"] is False
    assert oi["value"] is None
    msg = (oi["message"] or "").lower()
    assert "operating-income" in msg or "operating income" in msg
    assert "nonconsolidated" in msg
    assert "edinet_save_document" in (oi.get("next_step") or "")
    assert "type=2" in (oi.get("next_step") or "")


def test_financials_missing_metric_helper(plugin):
    # Empty ZIP with only headers → all missing
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        header = "要素ID\t項目名\tコンテキストID\t相対年度\t連結・個別\t期間・時点\tユニットID\t単位\t値\n"
        # utf-16 with BOM
        zf.writestr("XBRL_TO_CSV/empty.csv", header.encode("utf-16"))
    out = plugin.metrics.extract_financials(buf.getvalue(), doc_id="EMPTY")
    assert out["metrics"]["revenue"]["found"] is False
    assert out["metrics"]["revenue"]["value"] is None


# --- save -----------------------------------------------------------------------


def test_save_document_pdf_under_hermes_home(run, _isolate_hermes_home):
    out = run("edinet_save_document", {"doc_id": "S100TR7I", "type": 2})
    path = Path(out["path"])
    assert path.is_file()
    assert str(_isolate_hermes_home) in str(path)
    assert "jp-edinet" in str(path)
    assert path.read_bytes().startswith(b"%PDF")
    assert out["bytes"] > 0


def test_save_document_csv_type5(run, _isolate_hermes_home):
    out = run("edinet_save_document", {"doc_id": "S100TMMG", "type": 5})
    path = Path(out["path"])
    assert path.is_file()
    assert path.suffix == ".zip"
    assert path.stat().st_size == (FIXTURES / CSV_BY_DOC["S100TMMG"]).stat().st_size


# --- scrub / security -----------------------------------------------------------


def test_scrub_removes_subscription_key_from_errors(plugin):
    secret = "super-secret-key-xyz"
    dirty = f"https://api.edinet-fsa.go.jp/api/v2/documents.json?date=2024-06-25&Subscription-Key={secret}"
    clean = plugin.client.scrub(dirty, secret)
    assert secret not in clean
    assert "Subscription-Key=***" in clean or "Subscription-Key" not in clean.replace(
        "Subscription-Key=***", ""
    )
    assert "https://api.edinet-fsa.go.jp" not in clean


def test_tool_json_never_contains_subscription_key(run, monkeypatch, plugin):
    monkeypatch.setenv("EDINET_API_KEY", "leak-me-subscription-value")
    # Force an error path that includes a fabricated URL in an exception message path
    def boom(*a, **k):
        raise plugin.errors.EdinetError(
            "failed Subscription-Key=leak-me-subscription-value "
            "https://api.edinet-fsa.go.jp/api/v2/x?Subscription-Key=leak-me-subscription-value",
            kind="http_error",
            next_step="retry",
        )

    monkeypatch.setattr(plugin.client, "fetch_document_bytes", boom)
    raw = plugin.tools.HANDLERS["edinet_financials"]({"doc_id": "S100TR7I"})
    assert "leak-me-subscription-value" not in raw
    assert "Subscription-Key=leak" not in raw
    assert "Subscription-Key" not in raw or "Subscription-Key=***" in raw


def test_network_host_constant(plugin):
    assert plugin.client.API_HOST == "api.edinet-fsa.go.jp"
    assert plugin.client.API_BASE.startswith("https://api.edinet-fsa.go.jp/")


def test_fixtures_readme_attributes_edinet():
    text = (FIXTURES / "README.md").read_text(encoding="utf-8")
    assert "EDINET" in text
    assert "Subscription-Key" not in text
    assert "2024" in text


def test_license_and_notice_exist():
    assert (PLUGIN_DIR / "LICENSE").is_file()
    assert (PLUGIN_DIR / "NOTICE").is_file()
    notice = (PLUGIN_DIR / "NOTICE").read_text(encoding="utf-8")
    assert "EDINET" in notice
    assert "Unofficial" not in notice
    assert "edinet-tools-style" not in notice
    assert "Apache-licensed" not in notice
    assert "WZEK0030" in notice or "PDL1.0" in notice


# --- HTTP-200 JSON error envelopes / audit fixes --------------------------------


def test_list_fetch_401_json_is_api_key_error_and_not_cached(
    plugin, http_bytes, _isolate_hermes_home
):
    http_bytes["handler"] = lambda path, params, api_key: (
        FIXTURES / "error_invalid_key.json"
    ).read_bytes()

    with pytest.raises(plugin.errors.ApiKeyError) as caught:
        plugin.client.fetch_documents_list("2024-06-25")
    assert caught.value.kind == "api_key_missing_or_invalid"
    assert caught.value.status == 401

    raw = plugin.tools.HANDLERS["edinet_search_filings"](
        {
            "sec_code": "7203",
            "date_from": "2024-06-25",
            "date_to": "2024-06-25",
        }
    )
    out = json.loads(raw)
    assert out["kind"] == "api_key_missing_or_invalid"
    assert out.get("status") == 401
    assert "next_step" in out
    lists_root = _isolate_hermes_home / "plugin-data" / "jp-edinet" / "lists"
    assert list(lists_root.rglob("2024-06-25.json")) == []


def test_save_document_404_json_does_not_write_pdf(plugin, http_bytes, _isolate_hermes_home):
    http_bytes["handler"] = lambda path, params, api_key: (
        FIXTURES / "error_doc_not_found.json"
    ).read_bytes()
    out = json.loads(
        plugin.tools.HANDLERS["edinet_save_document"]({"doc_id": "S100MISS", "type": 2})
    )
    assert "error" in out
    assert out["kind"] == "http_error"
    assert out.get("status") == 400
    assert "not treated as a missing document" in out.get("next_step", "")
    assert "outside retention" not in out.get("next_step", "")
    docs = _isolate_hermes_home / "plugin-data" / "jp-edinet" / "docs"
    if docs.is_dir():
        assert list(docs.glob("*.pdf")) == []


def test_metadata_status_400_is_not_not_found(plugin):
    raw = (
        b'{"metadata":{"status":"400","message":"Bad Request"},"results":null}'
    )
    with pytest.raises(plugin.errors.EdinetError) as caught:
        plugin.client._classify_edinet_body(raw, api_key=FAKE_API_KEY, resource="document")
    assert caught.value.kind == "http_error"
    assert caught.value.status == 400
    assert "Only 404 is not_found" in caught.value.next_step
    assert "8 characters" not in caught.value.next_step
    assert "edinet_search_filings" in caught.value.next_step


def test_save_document_rejects_json_pretending_to_be_pdf(plugin, monkeypatch, _isolate_hermes_home):
    monkeypatch.setenv("EDINET_API_KEY", FAKE_API_KEY)
    monkeypatch.setattr(
        plugin.client,
        "fetch_document_bytes",
        lambda doc_id, doc_type: b'{"StatusCode":200,"message":"not really a pdf"}',
    )
    out = json.loads(
        plugin.tools.HANDLERS["edinet_save_document"]({"doc_id": "S100TR7I", "type": 2})
    )
    assert "error" in out
    assert out["kind"] == "bad_response"
    assert "PDF" in out["error"] or "magic" in out["error"].lower()
    docs = _isolate_hermes_home / "plugin-data" / "jp-edinet" / "docs"
    if docs.is_dir():
        assert list(docs.glob("*.pdf")) == []


def test_financials_deletes_poisoned_non_zip_cache(plugin, monkeypatch, _isolate_hermes_home):
    monkeypatch.setenv("EDINET_API_KEY", FAKE_API_KEY)
    doc_id = "S100TR7I"
    path = plugin.tools._csv_cache_path(doc_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'{"StatusCode":401,"message":"Access denied due to invalid subscription key."}')

    good = (FIXTURES / CSV_BY_DOC[doc_id]).read_bytes()
    calls = {"n": 0}

    def fake_bytes(did, doc_type):
        calls["n"] += 1
        assert did == doc_id and doc_type == 5
        return good

    monkeypatch.setattr(plugin.client, "fetch_document_bytes", fake_bytes)
    out = json.loads(plugin.tools.HANDLERS["edinet_financials"]({"doc_id": doc_id}))
    assert "error" not in out
    assert out["metrics"]["revenue"]["found"] is True
    assert calls["n"] == 1
    assert path.is_file()
    assert path.read_bytes().startswith(b"PK")


def test_extraordinary_maps_to_180_not_160(plugin):
    codes = plugin.tools.DOC_TYPE_GROUPS["extraordinary"]
    assert "180" in codes
    assert "190" in codes
    assert "160" not in codes
    assert "160" in plugin.tools.DOC_TYPE_GROUPS["semiannual"]
    assert "170" in plugin.tools.DOC_TYPE_GROUPS["amendment"]
    assert "135" not in plugin.tools.DOC_TYPE_GROUPS["amendment"]
    assert "136" not in plugin.tools.DOC_TYPE_GROUPS["amendment"]
    assert plugin.tools.DOC_TYPE_GROUPS["annual"] == plugin.tools.DOC_TYPE_GROUPS["yuho"]


def test_schemas_and_readme_list_semiannual_and_annual(plugin):
    schema = plugin.schemas.SEARCH_FILINGS
    blob = schema["description"] + schema["parameters"]["properties"]["doc_types"]["description"]
    assert "semiannual" in blob
    assert "annual" in blob
    readme = (PLUGIN_DIR / "README.md").read_text(encoding="utf-8")
    assert "`semiannual`" in readme
    assert "`annual`" in readme
    assert "million yen" not in readme
    assert "45,095,325,000,000 円" in readme
    assert "lists/<api-key-fingerprint>/YYYY-MM-DD.json" in readme
    assert "lists/<api-key-fingerprint>/" in plugin.schemas.SEARCH_FILINGS["description"]
    assert "api-key-fingerprint" in plugin.schemas.SAVE_DOCUMENT["description"]
    assert "api-key-fingerprint" in readme
    assert plugin.client.CALL_BUDGET_SECONDS >= 180
    assert "net_sales" not in plugin.schemas.FINANCIALS["description"]
    assert "Revenue (営業収益)" not in readme
    assert "fallback" in readme.lower() or "項目名" in readme


def test_financials_does_not_alias_revenue_as_net_sales(run):
    out = run("edinet_financials", {"doc_id": "S100TR7I"})
    assert "net_sales" not in out["metrics"]
    assert out["metrics"]["revenue"]["found"] is True


def test_unknown_doc_type_next_step_lists_aliases(plugin):
    with pytest.raises(plugin.errors.EdinetError) as caught:
        plugin.tools._expand_doc_types(["not-a-real-alias"])
    assert "semiannual" in caught.value.next_step
    assert "annual" in caught.value.next_step


def test_metadata_status_401_is_api_key_error(plugin):
    raw = (
        b'{"metadata":{"status":"401","message":"Unauthorized"},"results":null}'
    )
    with pytest.raises(plugin.errors.ApiKeyError) as caught:
        plugin.client._classify_edinet_body(raw, api_key=FAKE_API_KEY)
    assert caught.value.status == 401
    assert caught.value.kind == "api_key_missing_or_invalid"


def test_metadata_status_403_is_api_key_error(plugin):
    raw = b'{"metadata":{"status":403,"message":"Forbidden"},"results":null}'
    with pytest.raises(plugin.errors.ApiKeyError) as caught:
        plugin.client._classify_edinet_body(raw, api_key=FAKE_API_KEY)
    assert caught.value.status == 403


def test_zip_rejects_oversized_declared_member(plugin, monkeypatch):
    import io
    import zipfile

    monkeypatch.setattr(plugin.save, "MAX_ZIP_MEMBER_UNCOMPRESSED", 100)
    monkeypatch.setattr(plugin.save, "MAX_ZIP_TOTAL_UNCOMPRESSED", 10_000)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("big.csv", b"x" * 200)
    with pytest.raises(plugin.errors.EdinetError) as caught:
        plugin.save.assert_document_bytes(buf.getvalue(), 5)
    assert caught.value.kind == "bad_response"
    assert "uncompressed" in caught.value.args[0].lower() or "size" in caught.value.args[0].lower()


def test_iter_csv_rows_rejects_oversized_zip(plugin, monkeypatch):
    import io
    import zipfile

    monkeypatch.setattr(plugin.save, "MAX_ZIP_MEMBER_UNCOMPRESSED", 50)
    monkeypatch.setattr(plugin.save, "MAX_ZIP_TOTAL_UNCOMPRESSED", 50)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("jpcrp.csv", b"x" * 80)
    with pytest.raises(plugin.errors.EdinetError) as caught:
        plugin.metrics.iter_csv_rows(buf.getvalue())
    assert caught.value.kind == "bad_response"


def test_live_request_interval_is_at_least_one_second(plugin):
    assert plugin.client.MIN_REQUEST_INTERVAL_SECONDS >= 1.0


def test_docs_cache_is_keyed_by_api_fingerprint(plugin, monkeypatch, _isolate_hermes_home):
    monkeypatch.setenv("EDINET_API_KEY", FAKE_API_KEY)
    path_a = plugin.cache.docs_dir()
    monkeypatch.setenv("EDINET_API_KEY", "other-edinet-key-DO-NOT-USE-1111")
    path_b = plugin.cache.docs_dir()
    assert path_a != path_b
    assert path_a.parent == path_b.parent


def test_should_retry_excludes_bad_response(plugin):
    retry = plugin.client._should_retry
    assert retry(plugin.errors.EdinetError("x", kind="rate_limited", status=429))
    assert retry(plugin.errors.EdinetError("x", kind="http_error", status=503))
    assert retry(plugin.errors.EdinetError("x", kind="network_error"))
    assert not retry(plugin.errors.EdinetError("x", kind="bad_response"))
    assert not retry(plugin.errors.EdinetError("x", kind="not_found", status=404))


def test_list_cache_write_respects_write_guard(plugin, monkeypatch, _isolate_hermes_home):
    from datetime import date
    import sys
    import types

    monkeypatch.setenv("EDINET_API_KEY", FAKE_API_KEY)

    def deny(path, *, verb="Write", entry=False):
        return f"Write denied: '{path}'"

    fake = types.ModuleType("agent")
    fake_fs = types.ModuleType("agent.file_safety")
    fake_fs.get_write_denied_error = deny
    sys.modules["agent"] = fake
    sys.modules["agent.file_safety"] = fake_fs
    day = date(2024, 6, 25)
    payload = json.loads((FIXTURES / "list_2024-06-25.json").read_text(encoding="utf-8"))
    path = plugin.cache._list_path(day)
    try:
        with pytest.raises(plugin.errors.EdinetError) as caught:
            plugin.cache.write_cached_list(day, payload)
        assert caught.value.kind == "access_denied"
        assert not path.exists()
    finally:
        sys.modules.pop("agent.file_safety", None)
        sys.modules.pop("agent", None)


def test_list_get_write_guard_before_fetch(plugin, monkeypatch, _isolate_hermes_home):
    from datetime import date
    import sys
    import types

    monkeypatch.setenv("EDINET_API_KEY", FAKE_API_KEY)
    fetched = {"n": 0}

    def fake_list(day: str):
        fetched["n"] += 1
        return {
            "metadata": {"status": "200", "message": "OK", "resultset": {"count": 0}},
            "results": [],
        }

    monkeypatch.setattr(plugin.client, "fetch_documents_list", fake_list)

    def deny(path, *, verb="Write", entry=False):
        return f"Write denied: '{path}'"

    fake = types.ModuleType("agent")
    fake_fs = types.ModuleType("agent.file_safety")
    fake_fs.get_write_denied_error = deny
    sys.modules["agent"] = fake
    sys.modules["agent.file_safety"] = fake_fs
    try:
        with pytest.raises(plugin.errors.EdinetError) as caught:
            plugin.cache.get_list_for_date(date(2024, 6, 25))
        assert caught.value.kind == "access_denied"
        assert fetched["n"] == 0
    finally:
        sys.modules.pop("agent.file_safety", None)
        sys.modules.pop("agent", None)


def test_search_skips_list_not_found_within_cap(plugin, monkeypatch, _isolate_hermes_home, run):
    monkeypatch.setenv("EDINET_API_KEY", FAKE_API_KEY)

    def fake_list(day: str):
        if day == "2024-06-24":
            raise plugin.errors.EdinetError(
                "EDINET returned metadata.status 404.",
                kind="not_found",
                status=404,
                next_step="Pick a file date still in retention.",
            )
        if day == "2024-06-25":
            return json.loads(
                (FIXTURES / "list_2024-06-25.json").read_text(encoding="utf-8")
            )
        return {
            "metadata": {"status": "200", "message": "OK", "resultset": {"count": 0}},
            "results": [],
        }

    monkeypatch.setattr(plugin.client, "fetch_documents_list", fake_list)
    monkeypatch.setattr(plugin.client, "RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr(plugin.client, "MIN_REQUEST_INTERVAL_SECONDS", 0)
    out = run(
        "edinet_search_filings",
        {
            "sec_code": "7203",
            "date_from": "2024-06-24",
            "date_to": "2024-06-25",
            "doc_types": ["yuho"],
        },
    )
    assert "error" not in out
    assert out["count"] >= 1
    assert out["filings"][0]["docID"] == "S100TR7I"
    assert "2024-06-24" in (out.get("cache") or {}).get("days_list_unavailable", [])
    assert any("not_found" in w.lower() for w in out.get("warnings", []))


def test_company_name_schema_warns_partial_match(plugin):
    desc = plugin.schemas.SEARCH_FILINGS["parameters"]["properties"]["company_name"][
        "description"
    ]
    assert "substring" in desc.lower() or "Substring" in desc
    assert "sec_code" in desc or "edinet_code" in desc


def test_zip_read_enforces_actual_byte_cap(plugin, monkeypatch):
    import io
    import zipfile

    monkeypatch.setattr(plugin.save, "MAX_ZIP_MEMBER_UNCOMPRESSED", 5_000)
    monkeypatch.setattr(plugin.save, "MAX_ZIP_TOTAL_UNCOMPRESSED", 5_000)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("jpcrp.csv", b"y" * 20_000)
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zf:
        with pytest.raises(plugin.errors.EdinetError) as caught:
            plugin.save.read_zip_member_limited(zf, "jpcrp.csv", total_so_far=0)
    assert caught.value.kind == "bad_response"
    assert "expanded" in caught.value.args[0].lower()


def test_zip_rejects_unsafe_member_paths(plugin):
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("../escape.csv", b"x")
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zf:
        with pytest.raises(plugin.errors.EdinetError) as caught:
            plugin.save.assert_zip_uncompressed_limits(zf)
    assert caught.value.kind == "bad_response"
    assert "unsafe" in caught.value.args[0].lower()


def test_search_empty_days_vs_filter_mismatch(run):
    empty_days = run(
        "edinet_search_filings",
        {
            "sec_code": "7203",
            "date_from": "2024-01-01",
            "date_to": "2024-01-01",
        },
    )
    assert empty_days["count"] == 0
    assert "list was empty" in empty_days["message"].lower()
    assert "weekends" in empty_days["next_step"].lower() or "filing day" in empty_days["next_step"].lower()

    filter_miss = run(
        "edinet_search_filings",
        {
            "sec_code": "9999",
            "date_from": "2024-06-25",
            "date_to": "2024-06-25",
        },
    )
    assert filter_miss["count"] == 0
    assert "none matched the filters" in filter_miss["message"].lower()
    step = filter_miss["next_step"].lower()
    assert "sec_code" in step or "edinet_code" in step or "company_name" in step


def test_max_calendar_days_arg_clamped_to_plugin_config(plugin, monkeypatch, api):
    plugin.settings.bind_config(lambda key, default=None: 31 if key == "max_calendar_days" else default)
    # 40-day span still errors when effective max is 31 (arg 999 clamped).
    out = json.loads(
        plugin.tools.HANDLERS["edinet_search_filings"](
            {
                "sec_code": "7203",
                "date_from": "2024-01-01",
                "date_to": "2024-02-09",  # 40 calendar days
                "max_calendar_days": 999,
            }
        )
    )
    assert out["kind"] == "range_too_large"
    assert out["max_calendar_days"] == 31
    assert out["calendar_days"] == 40
    assert any("999" in w and "31" in w for w in out.get("warnings", []))

    # Within clamped max: succeeds and surfaces a warning about the cap.
    ok = json.loads(
        plugin.tools.HANDLERS["edinet_search_filings"](
            {
                "sec_code": "7203",
                "date_from": "2024-06-25",
                "date_to": "2024-06-25",
                "max_calendar_days": 999,
            }
        )
    )
    assert "error" not in ok
    assert ok["max_calendar_days"] == 31
    assert any("31" in w and "999" in w for w in ok.get("warnings", []))


def test_max_calendar_days_config_clamped_to_absolute_maximum(plugin):
    plugin.settings.bind_config(
        lambda key, default=None: 365 if key == "max_calendar_days" else default
    )
    assert plugin.settings.max_calendar_days() == 31
    assert plugin.settings.ABSOLUTE_MAX_CALENDAR_DAYS == 31


def test_search_limit_sets_truncated_flag(run):
    out = run(
        "edinet_search_filings",
        {
            "sec_code": "7203",
            "date_from": "2024-06-25",
            "date_to": "2024-06-25",
            "limit": 1,
        },
    )
    assert "error" not in out
    assert out["truncated"] is True
    assert out["count"] == 1
    assert out.get("date_scan_stopped_at") == "2024-06-25"
    assert "limit" in (out.get("message") or "").lower()


def test_citation_helper_matches_tos_shape(plugin):
    line = plugin.citation.edinet_citation(doc_id="S100TR7I")
    assert "EDINET閲覧（提出）サイト" in line
    assert "https://disclosure2.edinet-fsa.go.jp/WZEK0040.aspx?S100TR7I" in line
    assert "digital.go.jp/resources/open_data/public_data_license_v1.0" in line
    assert "WZEK0030.html" not in line
    assert "書類管理番号" not in line
    assert "より抜粋して作成" not in line
    assert plugin.citation.excerpt_note(doc_id="S100TR7I") == (
        "EDINET閲覧（提出）サイト（https://disclosure2.edinet-fsa.go.jp/WZEK0040.aspx?S100TR7I）より抜粋して作成"
    )
    assert plugin.citation.edinet_citation() == (
        "出典：EDINET閲覧（提出）サイト（https://disclosure2.edinet-fsa.go.jp/）、"
        "PDL1.0（https://www.digital.go.jp/resources/open_data/public_data_license_v1.0）"
    )


def test_search_attaches_filing_page_and_cites_single_hit(run):
    out = run(
        "edinet_search_filings",
        {
            "sec_code": "7203",
            "date_from": "2024-06-25",
            "date_to": "2024-06-25",
            "doc_types": ["yuho"],
        },
    )
    assert "error" not in out
    assert out["count"] == 1
    assert out["filings"][0]["filing_page"].endswith("WZEK0040.aspx?S100TR7I")
    assert "WZEK0040.aspx?S100TR7I" in out["source"]["citation"]
    assert "WZEK0040.aspx?S100TR7I" in out["source"]["excerpt_note"]
    assert "より抜粋して作成" in out["source"]["excerpt_note"]
    assert "より抜粋して作成" not in out["source"]["citation"]


def test_search_multi_hit_overall_citation_is_portal(run):
    out = run(
        "edinet_search_filings",
        {
            "company_name": "株式会社",
            "date_from": "2024-06-25",
            "date_to": "2024-06-25",
            "limit": 5,
        },
    )
    assert "error" not in out
    assert out["count"] >= 2
    assert "WZEK0040.aspx?" not in out["source"]["citation"]
    assert "disclosure2.edinet-fsa.go.jp/" in out["source"]["citation"]
    assert "WZEK0040.aspx?" not in out["source"]["excerpt_note"]
    assert "より抜粋して作成" in out["source"]["excerpt_note"]
    assert any("substring" in w.lower() for w in out.get("warnings", []))
    assert out["filings"][0].get("filing_page", "").startswith(
        "https://disclosure2.edinet-fsa.go.jp/WZEK0040.aspx?"
    )


def test_financials_cache_path_is_separate_from_save_type5(run, plugin):
    fin = run("edinet_financials", {"doc_id": "S100TR7I"})
    assert "error" not in fin
    cache_path = Path(fin["csv_path"])
    assert "financials" in cache_path.parts
    saved = run("edinet_save_document", {"doc_id": "S100TR7I", "type": 5})
    assert "error" not in saved
    save_path = Path(saved["path"])
    assert save_path != cache_path
    assert save_path.name == "S100TR7I_type5.zip"
    assert "financials" not in save_path.parts
    # Expiring financials cache must not remove the saved type=5 file.
    old = time.time() - plugin.cache.DOCS_CSV_TTL_SECONDS - 10
    os.utime(cache_path, (old, old))
    again = run("edinet_financials", {"doc_id": "S100TR7I"})
    assert "error" not in again
    assert save_path.is_file()


def test_normalize_doc_id_uppercases(plugin):
    assert plugin.save.normalize_doc_id("s100tr7i") == "S100TR7I"


def test_doc_id_wrong_length_is_rejected_before_request(plugin, monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("API was called")

    monkeypatch.setattr(plugin.client, "fetch_document_bytes", boom)
    out = json.loads(
        plugin.tools.HANDLERS["edinet_financials"]({"doc_id": "S100NOSUCH"})
    )
    assert out["kind"] == "bad_request"
    assert "8 characters" in out["next_step"]


def test_raw_http_400_is_not_not_found(plugin):
    caught = plugin.client._http_error(400, b"bad", FAKE_API_KEY, resource="list")
    assert caught.kind == "http_error"
    assert caught.status == 400
    assert "whole" in caught.next_step
    assert "not_found" not in caught.kind
    document = plugin.client._http_error(400, b"bad", FAKE_API_KEY, resource="document")
    assert document.kind == "http_error"
    assert "8 characters" not in document.next_step
    assert "edinet_search_filings" in document.next_step
    assert "type" in document.next_step
    missing = plugin.client._http_error(404, b"missing", FAKE_API_KEY, resource="list")
    assert missing.kind == "not_found"
    assert missing.status == 404


def test_search_schema_requires_a_company_identifier(plugin):
    params = plugin.schemas.SEARCH_FILINGS["parameters"]
    assert "date_from" in params["required"] and "date_to" in params["required"]
    any_of = params.get("anyOf") or []
    keys = {tuple(sorted(item.get("required") or [])) for item in any_of}
    assert ("company_name",) in keys
    assert ("sec_code",) in keys
    assert ("edinet_code",) in keys
    assert ("corporate_number",) in keys


def test_financial_values_are_json_integers(run):
    out = run("edinet_financials", {"doc_id": "S100TR7I"})
    rev = out["metrics"]["revenue"]["value"]
    assert isinstance(rev, int)
    assert not isinstance(rev, bool)
    assert rev == 45095325000000
    assert "owners_equity" in (out["metrics"]["net_assets"].get("meaning") or "")
    assert out["metrics"]["revenue"].get("label")
    assert "営業収益" in (out["metrics"]["revenue"].get("label") or "")


def test_japan_gaap_net_assets_meaning_is_total_net_assets(run):
    out = run("edinet_financials", {"doc_id": "S100TMMG"})
    assert out["accounting_standard"] == "Japan GAAP"
    na = out["metrics"]["net_assets"]
    assert na["found"] is True
    assert na["value"] == 2604998000000
    assert "NetAssetsSummaryOfBusinessResults" in (na.get("element_id") or "")
    assert "total_net_assets" in (na.get("meaning") or "")
    assert "純資産合計" in (na.get("meaning") or "")


def test_unknown_numeric_doc_type_is_bad_request(run):
    out = run(
        "edinet_search_filings",
        {
            "sec_code": "7203",
            "date_from": "2024-06-25",
            "date_to": "2024-06-25",
            "doc_types": ["999"],
        },
    )
    assert out.get("kind") == "bad_request"
    assert "999" in (out.get("error") or "")


def test_save_includes_citation(run):
    out = run("edinet_save_document", {"doc_id": "S100TR7I", "type": 2})
    assert "error" not in out
    assert "WZEK0040.aspx?S100TR7I" in out["source"]["citation"]
    assert "より抜粋して作成" in out["source"]["excerpt_note"]


def test_revenue_matches_sales_and_financial_services_ifrs_key_data(plugin):
    """Sony-style headline tag (売上高及び金融ビジネス収入) must beat bare NetSalesIFRS."""
    import io
    import zipfile

    header = (
        "要素ID\t項目名\tコンテキストID\t相対年度\t連結・個別\t期間・時点\tユニットID\t単位\t値\n"
    )
    rows = [
        header,
        "jpdei_cor:AccountingStandardsDEI\t\tFilingDateInstant\t\t\t\t\t\tIFRS\n",
        (
            "jpigp_cor:NetSalesIFRS\t売上高\tCurrentYearDuration\t当期\tその他\t期間\t\t円\t"
            "11260000000000\n"
        ),
        (
            "jpcrp030000-asr_E01777-000:SalesAndFinancialServicesRevenueIFRSKeyFinancialData\t"
            "売上高及び金融ビジネス収入\tCurrentYearDuration\t当期\tその他\t期間\t\t円\t"
            "13020768000000\n"
        ),
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("XBRL_TO_CSV/jpcrp.csv", "".join(rows).encode("utf-16"))
    out = plugin.metrics.extract_financials(buf.getvalue(), doc_id="S100TS7P")
    rev = out["metrics"]["revenue"]
    assert rev["found"] is True
    assert rev["value"] == 13020768000000
    assert "SalesAndFinancialServicesRevenueIFRSKeyFinancialData" in rev["element_id"]


def test_success_list_payload_requires_metadata_status(plugin):
    assert plugin.cache.is_success_list_payload(
        {"metadata": {"status": "200", "resultset": {"count": 0}}, "results": []}
    )
    assert not plugin.cache.is_success_list_payload(
        {"metadata": {"status": "200"}, "results": []}
    )
    assert not plugin.cache.is_success_list_payload(
        {
            "metadata": {"status": "200", "resultset": {"count": 2}},
            "results": [{}],
        }
    )
    assert not plugin.cache.is_success_list_payload({"results": []})
    assert not plugin.cache.is_success_list_payload(
        {"metadata": {}, "results": []}
    )


def test_financials_csv_cache_ttl_and_warning(run, plugin):
    first = run("edinet_financials", {"doc_id": "S100TR7I"})
    assert "error" not in first
    assert first["csv_cached"] is False
    second = run("edinet_financials", {"doc_id": "S100TR7I"})
    assert second["csv_cached"] is True
    assert any("TTL" in w for w in second.get("warnings", []))
    path = plugin.tools._csv_cache_path("S100TR7I")
    old = time.time() - plugin.cache.DOCS_CSV_TTL_SECONDS - 10
    os.utime(path, (old, old))
    third = run("edinet_financials", {"doc_id": "S100TR7I"})
    assert third["csv_cached"] is False


def test_member_and_kobetsu_rows_are_not_headline_figures(plugin):
    """Segment and equity-component rows must not become the company total."""
    import io
    import zipfile

    header = "要素ID\t項目名\tコンテキストID\t相対年度\t連結・個別\t期間・時点\tユニットID\t単位\t値\n"
    rows = [
        header,
        "jpdei_cor:AccountingStandardsDEI\t\tFilingDateInstant\t\t\t\t\t\tIFRS\n",
        (
            "jpcrp_cor:RevenueIFRSSummaryOfBusinessResults\t売上高\t"
            "CurrentYearDuration_ReportableSegmentMember\t当期\t連結\t期間\t\t円\t111\n"
        ),
        (
            "jpcrp_cor:RevenueIFRSSummaryOfBusinessResults\t売上高\t"
            "CurrentYearDuration\t当期\tその他\t期間\t\t円\t999\n"
        ),
        (
            "jppfs_cor:NetAssets\t資本金\tCurrentYearInstant_CapitalStockMember\t"
            "当期\t連結\t時点\t\t円\t10\n"
        ),
        (
            "jppfs_cor:OperatingIncome\t営業利益\tCurrentYearDuration\t"
            "当期\t個別\t期間\t\t円\t42\n"
        ),
        (
            "jpcrp_cor:OperatingIncomeSummaryOfBusinessResults\t営業利益\t"
            "CurrentYearDuration\t当期\t連結\t期間\t\t円\t77\n"
        ),
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("XBRL_TO_CSV/jpcrp.csv", "".join(rows).encode("utf-16"))
    out = plugin.metrics.extract_financials(buf.getvalue(), doc_id="S100TEST")
    assert out["metrics"]["revenue"]["value"] == 999
    assert out["metrics"]["operating_income"]["value"] == 77
    assert out["metrics"]["net_assets"]["found"] is False
    assert out["metrics"]["net_assets"]["value"] is None


def test_financials_csv_flag_zero_is_not_not_found(plugin, monkeypatch, _isolate_hermes_home):
    monkeypatch.setenv("EDINET_API_KEY", FAKE_API_KEY)
    fetched = {"n": 0}

    def fake_fetch(doc_id, doc_type):
        fetched["n"] += 1
        return (FIXTURES / CSV_BY_DOC["S100TR7I"]).read_bytes()

    monkeypatch.setattr(plugin.client, "fetch_document_bytes", fake_fetch)
    day_dir = plugin.cache.lists_dir() / plugin.cache.api_key_fingerprint()
    day_dir.mkdir(parents=True)
    payload = {
        "metadata": {"status": "200", "resultset": {"count": 1}},
        "results": [{"docID": "S100TR7M", "csvFlag": "0"}],
    }
    (day_dir / "2024-06-25.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    out = json.loads(
        plugin.tools.HANDLERS["edinet_financials"]({"doc_id": "S100TR7M"})
    )
    assert out["kind"] == "no_csv"
    assert out["kind"] != "not_found"
    assert "no CSV" in out["error"]
    assert "この書類には CSV が無い" in out["error"]
    assert fetched["n"] == 0


def test_operating_income_missing_points_to_pdf(plugin):
    raw = (FIXTURES / CSV_BY_DOC["S100T58N"]).read_bytes()
    out = plugin.metrics.extract_financials(raw, doc_id="S100T58N")
    oi = out["metrics"]["operating_income"]
    assert oi["found"] is False
    msg = oi["message"].lower()
    assert "operating-income" in msg or "operating income" in msg
    assert "nonconsolidated" in msg
    assert "edinet_save_document" in oi["next_step"]
    assert "type=2" in oi["next_step"]
    assert out["source"]["excerpt_note"] == plugin.citation.excerpt_note(doc_id="S100T58N")
    assert "より抜粋して作成" not in out["source"]["citation"]


def test_future_date_rejected_before_network(plugin, monkeypatch, _isolate_hermes_home):
    monkeypatch.setenv("EDINET_API_KEY", FAKE_API_KEY)
    monkeypatch.setattr(
        plugin.client,
        "fetch_documents_list",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not hit network")),
    )
    out = json.loads(
        plugin.tools.HANDLERS["edinet_search_filings"](
            {
                "sec_code": "7203",
                "date_from": "2099-01-01",
                "date_to": "2099-01-02",
            }
        )
    )
    assert out["kind"] == "bad_request"
    assert "date_to" in out["next_step"].lower()
    assert "today" in out["next_step"].lower()


def test_write_guard_denial_does_not_write(plugin, monkeypatch, _isolate_hermes_home):
    monkeypatch.setenv("EDINET_API_KEY", FAKE_API_KEY)
    fetched = {"n": 0}

    def fake_fetch(doc_id, doc_type):
        fetched["n"] += 1
        return (FIXTURES / "sample.pdf").read_bytes()

    monkeypatch.setattr(plugin.client, "fetch_document_bytes", fake_fetch)

    def deny(path, *, verb="Write", entry=False):
        return f"Write denied: '{path}' is a protected system/credential file."

    import types
    import sys

    fake = types.ModuleType("agent")
    fake_fs = types.ModuleType("agent.file_safety")
    fake_fs.get_write_denied_error = deny
    sys.modules["agent"] = fake
    sys.modules["agent.file_safety"] = fake_fs
    try:
        out = json.loads(
            plugin.tools.HANDLERS["edinet_save_document"]({"doc_id": "S100TR7I", "type": 2})
        )
        assert "error" in out
        assert out["kind"] == "access_denied"
        assert fetched["n"] == 0
        docs = plugin.cache.docs_dir()
        assert list(docs.glob("*.pdf")) == []
    finally:
        sys.modules.pop("agent.file_safety", None)
        sys.modules.pop("agent", None)


def test_write_refuses_when_guard_unavailable(plugin, monkeypatch, _isolate_hermes_home):
    monkeypatch.setenv("EDINET_API_KEY", FAKE_API_KEY)
    fetched = {"n": 0}

    def fake_fetch(doc_id, doc_type):
        fetched["n"] += 1
        return (FIXTURES / "sample.pdf").read_bytes()

    monkeypatch.setattr(plugin.client, "fetch_document_bytes", fake_fetch)
    import sys
    import types

    sys.modules.pop("agent.file_safety", None)
    sys.modules.pop("agent", None)
    agent_pkg = types.ModuleType("agent")
    agent_pkg.__path__ = []  # package without file_safety submodule
    sys.modules["agent"] = agent_pkg
    try:
        out = json.loads(
            plugin.tools.HANDLERS["edinet_save_document"]({"doc_id": "S100TR7I", "type": 2})
        )
        assert "error" in out
        assert out["kind"] == "access_denied"
        assert "unavailable" in out["error"].lower() or "write guard" in out["error"].lower()
        assert fetched["n"] == 0
        docs = plugin.cache.docs_dir()
        assert list(docs.glob("*.pdf")) == []
    finally:
        sys.modules.pop("agent.file_safety", None)
        sys.modules.pop("agent", None)


def test_write_guard_exception_is_access_denied(plugin, monkeypatch, _isolate_hermes_home):
    monkeypatch.setenv("EDINET_API_KEY", FAKE_API_KEY)
    fetched = {"n": 0}

    def fake_fetch(doc_id, doc_type):
        fetched["n"] += 1
        return (FIXTURES / "sample.pdf").read_bytes()

    monkeypatch.setattr(plugin.client, "fetch_document_bytes", fake_fetch)

    def boom(path, **kwargs):
        raise RuntimeError("write guard blew up")

    import types
    import sys

    fake = types.ModuleType("agent")
    fake_fs = types.ModuleType("agent.file_safety")
    fake_fs.get_write_denied_error = boom
    sys.modules["agent"] = fake
    sys.modules["agent.file_safety"] = fake_fs
    try:
        out = json.loads(
            plugin.tools.HANDLERS["edinet_save_document"]({"doc_id": "S100TR7I", "type": 2})
        )
        assert out["kind"] == "access_denied"
        assert out["kind"] != "internal_error"
        assert "simpler arguments" not in (out.get("next_step") or "").lower()
        assert "write guard" in (out.get("next_step") or "").lower()
        assert fetched["n"] == 0
        assert list(plugin.cache.docs_dir().glob("*.pdf")) == []
    finally:
        sys.modules.pop("agent.file_safety", None)
        sys.modules.pop("agent", None)


def test_financials_write_guard_before_fetch(plugin, monkeypatch, _isolate_hermes_home):
    monkeypatch.setenv("EDINET_API_KEY", FAKE_API_KEY)
    fetched = {"n": 0}

    def fake_fetch(doc_id, doc_type):
        fetched["n"] += 1
        return (FIXTURES / CSV_BY_DOC["S100TR7I"]).read_bytes()

    monkeypatch.setattr(plugin.client, "fetch_document_bytes", fake_fetch)

    def deny(path, *, verb="Write", entry=False):
        return f"Write denied: '{path}'"

    import types
    import sys

    fake = types.ModuleType("agent")
    fake_fs = types.ModuleType("agent.file_safety")
    fake_fs.get_write_denied_error = deny
    sys.modules["agent"] = fake
    sys.modules["agent.file_safety"] = fake_fs
    try:
        out = json.loads(
            plugin.tools.HANDLERS["edinet_financials"]({"doc_id": "S100TR7I"})
        )
        assert "error" in out
        assert out["kind"] == "access_denied"
        assert fetched["n"] == 0
    finally:
        sys.modules.pop("agent.file_safety", None)
        sys.modules.pop("agent", None)


def test_list_not_found_next_step_does_not_imply_future_date(plugin):
    step = plugin.client._not_found_next_step(resource="list")
    assert "today or earlier" not in step.lower()
    assert "retention" in step.lower() or "file date" in step.lower()


def test_metadata_status_404_missing_doc_is_not_found(plugin):
    raw = b'{"metadata":{"status":"404","message":"Not Found"},"results":null}'
    with pytest.raises(plugin.errors.EdinetError) as caught:
        plugin.client._classify_edinet_body(raw, api_key=FAKE_API_KEY, resource="document")
    assert caught.value.kind == "not_found"
    assert caught.value.status == 404


def test_cached_error_envelope_is_deleted_on_read(plugin, monkeypatch, _isolate_hermes_home):
    from datetime import date

    monkeypatch.setenv("EDINET_API_KEY", FAKE_API_KEY)
    day = date(2024, 6, 25)
    path = plugin.cache._list_path(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        (FIXTURES / "error_invalid_key.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    assert plugin.cache.read_cached_list(day) is None
    assert not path.exists()


def test_list_cache_is_scoped_to_api_key(run, plugin, api, monkeypatch, _isolate_hermes_home):
    """A prior success under key A must not satisfy searches under key B."""
    args = {
        "sec_code": "7203",
        "date_from": "2024-06-25",
        "date_to": "2024-06-25",
    }
    first = run("edinet_search_filings", args)
    assert first["cache"]["days_fetched"] == 1
    path_a = plugin.cache._list_path(date(2024, 6, 25))
    assert path_a.is_file()

    monkeypatch.setenv("EDINET_API_KEY", "different-test-key-DO-NOT-USE-1111")
    second = run("edinet_search_filings", args)
    assert second["cache"]["days_fetched"] == 1
    assert second["cache"]["days_cache_hit"] == 0
    path_b = plugin.cache._list_path(date(2024, 6, 25))
    assert path_b.is_file()
    assert path_a != path_b
    assert path_a.is_file()  # key A cache remains, but is unused


def test_week_and_compact_dates_are_rejected(run, api):
    for bad in ("2024-W26-2", "20240625"):
        out = run(
            "edinet_search_filings",
            {
                "sec_code": "7203",
                "date_from": bad,
                "date_to": "2024-06-25",
            },
        )
        assert out["kind"] == "bad_request"
        assert "YYYY-MM-DD" in out["error"]
    assert api == []


def test_limit_zero_is_rejected(run, api):
    out = run(
        "edinet_search_filings",
        {
            "sec_code": "7203",
            "date_from": "2024-06-25",
            "date_to": "2024-06-25",
            "limit": 0,
        },
    )
    assert out["kind"] == "bad_request"
    assert api == []


def test_short_corporate_number_is_not_a_miss(run, api):
    out = run(
        "edinet_search_filings",
        {
            "corporate_number": "123",
            "date_from": "2024-06-25",
            "date_to": "2024-06-25",
        },
    )
    assert out["kind"] == "bad_request"
    assert "13" in out["error"]
    assert api == []


def test_exact_row_limit_on_last_day_is_not_truncated(run):
    args = {
        "sec_code": "7203",
        "date_from": "2024-06-25",
        "date_to": "2024-06-25",
    }
    full = run("edinet_search_filings", args)
    assert full["count"] >= 1
    assert full["truncated"] is False
    exact = run("edinet_search_filings", {**args, "limit": full["count"]})
    assert exact["count"] == full["count"]
    assert exact["truncated"] is False
    assert "date_scan_stopped_at" not in exact


def test_plugin_data_dir_failure_does_not_write_fallback(
    run, _stub_plugin_data_dir, _isolate_hermes_home
):
    def boom(_name):
        raise RuntimeError("unavailable")

    _stub_plugin_data_dir.plugin_data_dir = boom
    out = run(
        "edinet_search_filings",
        {
            "sec_code": "7203",
            "date_from": "2024-06-25",
            "date_to": "2024-06-25",
        },
    )
    assert out["kind"] == "access_denied"
    assert not (_isolate_hermes_home / "cache").exists()
    assert list((_isolate_hermes_home / "plugin-data").rglob("*.json")) == []


def test_saved_at_uses_japan_time(run):
    out = run("edinet_save_document", {"doc_id": "S100TR7I", "type": 2})
    assert out["saved_at"].endswith("+09:00")


def test_list_400_fails_the_range_and_is_not_cached(plugin, monkeypatch, _isolate_hermes_home, run):
    monkeypatch.setenv("EDINET_API_KEY", FAKE_API_KEY)

    def fake_list(day: str):
        raise plugin.errors.EdinetError(
            "EDINET returned metadata.status 400.",
            kind="http_error",
            status=400,
            next_step=(
                "EDINET returned 400 for this file date. The search fails for the whole "
                "date range. Only a 404 list is skipped as outside retention."
            ),
        )

    monkeypatch.setattr(plugin.client, "fetch_documents_list", fake_list)
    out = run(
        "edinet_search_filings",
        {"sec_code": "7203", "date_from": "2024-06-25", "date_to": "2024-06-25"},
    )
    assert out["kind"] == "http_error"
    assert out.get("status") == 400
    assert "whole date range" in out.get("next_step", "")
    lists_root = _isolate_hermes_home / "plugin-data" / "jp-edinet" / "lists"
    assert list(lists_root.rglob("*.json")) == []


def test_count_mismatch_is_not_cached(plugin, monkeypatch, _isolate_hermes_home):
    monkeypatch.setenv("EDINET_API_KEY", FAKE_API_KEY)

    def fake_list(_day: str):
        return {
            "metadata": {"status": "200", "message": "OK", "resultset": {"count": 9}},
            "results": [],
        }

    monkeypatch.setattr(plugin.client, "fetch_documents_list", fake_list)
    with pytest.raises(plugin.errors.EdinetError) as caught:
        plugin.cache.get_list_for_date(date(2024, 6, 25))
    assert caught.value.kind == "bad_response"
    assert "does not match" in str(caught.value)
    lists_root = _isolate_hermes_home / "plugin-data" / "jp-edinet" / "lists"
    assert list(lists_root.rglob("*.json")) == []


def test_sudden_zero_warns_until_rows_return(plugin, monkeypatch, run):
    monkeypatch.setenv("EDINET_API_KEY", FAKE_API_KEY)
    body = json.loads((FIXTURES / "list_2024-06-25.json").read_text(encoding="utf-8"))
    state = {"body": body}

    def fake_list(_day: str):
        return state["body"]

    monkeypatch.setattr(plugin.client, "fetch_documents_list", fake_list)
    monkeypatch.setattr(plugin.cache, "PAST_TTL_SECONDS", -1)
    first = run(
        "edinet_search_filings",
        {"sec_code": "7203", "date_from": "2024-06-25", "date_to": "2024-06-25"},
    )
    assert first["count"] >= 1
    assert not any("dropped from" in w for w in first.get("warnings") or [])

    state["body"] = {
        "metadata": {"status": "200", "message": "OK", "resultset": {"count": 0}},
        "results": [],
    }
    second = run(
        "edinet_search_filings",
        {"sec_code": "7203", "date_from": "2024-06-25", "date_to": "2024-06-25"},
    )
    assert second["count"] == 0
    assert any("dropped from" in w and "to 0" in w for w in second.get("warnings") or [])

    third = run(
        "edinet_search_filings",
        {"sec_code": "7203", "date_from": "2024-06-25", "date_to": "2024-06-25"},
    )
    assert any("dropped from" in w for w in third.get("warnings") or [])

    state["body"] = body
    fourth = run(
        "edinet_search_filings",
        {"sec_code": "7203", "date_from": "2024-06-25", "date_to": "2024-06-25"},
    )
    assert fourth["count"] >= 1
    assert not any("dropped from" in w for w in fourth.get("warnings") or [])
