"""Argument shapes from the 2026-10-14 EDINET sessions."""
import json
from datetime import date

import pytest


def test_slash_and_fullwidth_dates_parse(plugin):
    assert plugin.tools._parse_day("2024/06/25", "date_from") == date(2024, 6, 25)
    assert plugin.tools._parse_day("２０２４-０６-２５", "date_to") == date(2024, 6, 25)


def test_week_and_compact_dates_hide_the_input(plugin):
    for bad in ("2024-W26-2", "20240625", "June 25, 2024"):
        with pytest.raises(plugin.errors.EdinetError) as caught:
            plugin.tools._parse_day(bad, "date_from")
        text = str(caught.value)
        assert "2024-06-25" in text
        assert bad not in text
        assert "YYYY-MM-DD" in text


def test_future_day_names_an_example_and_hides_the_input(plugin, monkeypatch):
    monkeypatch.setenv("EDINET_API_KEY", "test-edinet-key-DO-NOT-USE-live-0000")
    # future_en
    out = json.loads(plugin.tools.edinet_search_filings({
        "sec_code": "7203",
        "date_from": "2099-01-01",
        "date_to": "2099-01-02",
    }))
    text = json.dumps(out, ensure_ascii=False)
    assert out["ok"] is False
    assert out["error_type"] == "bad_request"
    assert "2024-06-25" in out["next_step"]
    assert "2099" not in text


def test_fullwidth_sec_code_is_digits(plugin):
    assert plugin.tools._normalize_sec("７２０３") == "7203"


def test_bad_doc_id_hides_the_input(plugin, monkeypatch):
    monkeypatch.setenv("EDINET_API_KEY", "test-edinet-key-DO-NOT-USE-live-0000")
    # bad_doc_en sent NOTADOCU
    out = json.loads(plugin.tools.edinet_financials({"doc_id": "NOTADOCU"}))
    text = json.dumps(out, ensure_ascii=False)
    assert out["ok"] is False
    assert out["error_type"] == "bad_request"
    assert "S100TR7I" in out["error"]
    assert "NOTADOCU" not in text


def test_lower_case_doc_id_is_accepted_as_a_shape(plugin):
    assert plugin.save.normalize_doc_id("s100tr7i") == "S100TR7I"


def test_unexpected_error_also_has_ok_false_and_error_type(plugin, monkeypatch):
    monkeypatch.setenv("EDINET_API_KEY", "test-edinet-key-DO-NOT-USE-live-0000")

    def boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(plugin.save, "normalize_doc_id", boom)
    out = json.loads(plugin.tools.edinet_financials({"doc_id": "S100TR7I"}))
    assert out["ok"] is False
    assert out["kind"] == "internal_error"
    assert out["error_type"] == "internal_error"


def test_keyless_search_answers_before_a_guessed_file_date(plugin, monkeypatch):
    monkeypatch.delenv("EDINET_API_KEY", raising=False)
    out = json.loads(plugin.tools.edinet_search_filings({
        "company_name": "トヨタ自動車",
        "date_from": "2099-01-01",
        "date_to": "2099-01-01",
    }))
    text = json.dumps(out, ensure_ascii=False)
    assert out["kind"] == "api_key_missing_or_invalid"
    assert "WEEK0060" in out["next_step"]
    assert "filing-company" in out["next_step"]
    assert "consolidated" in out["next_step"]
    assert "2099" not in text
    assert "2024-06-25" not in text


def test_nudge_is_absent_on_a_settings_question(plugin):
    assert plugin._nudge_edinet(user_message="hermes の設定はどこで見る？") is None
    assert plugin._nudge_edinet(user_message="コーヒーの入れ方") is None


def test_nudge_does_not_name_a_fixed_file_date(plugin, monkeypatch):
    monkeypatch.delenv("EDINET_API_KEY", raising=False)
    missing = plugin._nudge_edinet(user_message="有報の連結の売上")["context"]
    assert "2024-06-25" not in missing
    assert "edinet_search_filings" in missing
    assert "company name only" in missing
    assert "Do not invent a file date" in missing
    assert "edinet_financials" not in missing
    assert "WEEK0060" in missing
    assert "four prior" in missing
    assert "EDINET filing URL" in missing
    assert "successfully retrieved" in missing
    assert "verified" in missing
    assert "obtained using" in missing
    assert "取得しました" in missing
    monkeypatch.setenv("EDINET_API_KEY", "test-edinet-key-DO-NOT-USE-live-0000")
    present = plugin._nudge_edinet(user_message="有報の連結の売上")["context"]
    assert "2024-06-25" not in present
    assert "edinet_financials" in present
    assert "filing_company" in present
    assert "Do not invent a file date" in present
    assert "not known yet" in present
    assert "only the file dates the user wrote" in present
    assert "successfully retrieved" in present
    assert "verified" in present
    assert "obtained using" in present


def test_nudge_does_not_call_the_key_unset_when_the_helper_cannot_be_read(plugin, monkeypatch):
    monkeypatch.setattr(plugin.client, "key_is_set", lambda: None)
    text = plugin._nudge_edinet(user_message="有報の連結の売上")["context"]
    assert "is not set" not in text
    assert "WEEK0060" not in text
    assert "could not be read" in text
    assert "Do not invent a file date" in text
    assert "successfully retrieved" in text
    assert "verified" in text
    assert "obtained using" in text


def test_key_is_set_is_unknown_when_get_secret_raises(plugin, monkeypatch):
    import agent.secret_scope as scope

    def boom(name, default=None):
        raise RuntimeError("unread")

    monkeypatch.setattr(scope, "get_secret", boom)
    assert plugin.client.key_is_set() is None


def test_key_is_set_is_unknown_when_the_helper_cannot_be_imported(plugin, monkeypatch):
    import builtins
    import sys

    saved = sys.modules.pop("agent.secret_scope", None)
    agent = sys.modules.get("agent")
    had = getattr(agent, "secret_scope", None) if agent is not None else None
    if agent is not None and hasattr(agent, "secret_scope"):
        delattr(agent, "secret_scope")
    real_import = builtins.__import__

    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "agent.secret_scope":
            raise ImportError("blocked")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded)
    try:
        assert plugin.client.key_is_set() is None
    finally:
        if saved is not None:
            sys.modules["agent.secret_scope"] = saved
            if agent is not None:
                setattr(agent, "secret_scope", had)
