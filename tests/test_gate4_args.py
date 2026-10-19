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


def test_future_day_names_an_example_and_hides_the_input(plugin):
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


def test_bad_doc_id_hides_the_input(plugin):
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
    def boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(plugin.save, "normalize_doc_id", boom)
    out = json.loads(plugin.tools.edinet_financials({"doc_id": "S100TR7I"}))
    assert out["ok"] is False
    assert out["kind"] == "internal_error"
    assert out["error_type"] == "internal_error"
