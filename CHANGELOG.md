# Changelog

## 0.1.10

What was understood: company talk treated two katakana characters followed by kanji as a registry name, so a filing paragraph was added for フィボナッチ数列 and グラタン皿. A company name with no filing word, such as トヨタ自動車, also received the filing paragraph. Pytest before this change, on 0.1.9: 112 passed, 2 skipped.

What changed: `pre_llm_call` adds a paragraph only on a filing question (有報, 有価証券, 連結, 提出会社, the word EDINET, securities, or consolidated) or when an EDINET tool was called in the previous turn. The decision is `turn_scope.domains`, copied into this plugin and not imported from another. A katakana-kanji shape is not a name. Pytest on this commit: 111 passed, 6 skipped.

## 0.1.9

What was understood: `pre_llm_call` added one of the three EDINET paragraphs on every turn, including a settings question and a question that is not about a company or a filing. Pytest before this change, on 0.1.8: 111 passed, 2 skipped.

What changed: the same three paragraphs are added only when the user message is company talk or a filing question. Company talk is a 13-digit corporate number, a legal-form word, a registry-style name such as トヨタ自動車, or a tool name from the six books. Other turns get no extra context. Pytest on this commit: 112 passed, 2 skipped.

## 0.1.8

What was understood: the skill and `pre_llm_call` forbade 取得しました and 確認できます. They did not forbid "successfully retrieved", "verified", or "obtained using". Pytest before this change, on 0.1.7: 111 passed, 2 skipped.

What changed: those three English phrases are in the same ban as 取得しました, in the skill and in every `pre_llm_call` branch. Pytest on this commit: 111 passed, 2 skipped.

## 0.1.7

What was understood: `key_is_set` returned False both when `agent.secret_scope` could not be imported and when `get_secret` raised. `pre_llm_call` then said `EDINET_API_KEY is not set`. An empty read from the helper is a different case: the key is unset and the free-key steps belong in the reply. Pytest before this change, on 0.1.6: 108 passed, 2 skipped.

What changed: `key_is_set` returns None when the helper cannot be imported or read, and False only when the helper returns an empty value. When the result is None, `pre_llm_call` does not say the key is unset and does not include the free-key steps. An empty key still includes those steps. Pytest on this commit: 111 passed, 2 skipped.

## 0.1.6

What was understood: without `EDINET_API_KEY`, `pre_llm_call` told the model to call `edinet_search_filings` with the company name only and to copy `next_step`. On a small model that tool was not called, so the reply did not contain the free-key steps, the five periods, the unit, or the filing URL. With a key, `pre_llm_call` named `edinet_financials` and did not say what to do before a document id is known. Pytest on this commit: 108 passed, 2 skipped.

What changed: the keyless `pre_llm_call` includes the same `next_step` the tool returns (`WEEK0060`, the consolidated six lines and the filing-company six lines, the current period and up to four prior periods, the unit, and the EDINET filing URL once a document id is known). It still does not name a file date and does not name `edinet_financials`. When the key is set and no document id has been returned, `pre_llm_call` names `edinet_search_filings` with only the file dates the user wrote, and says the document id is not known yet.

## 0.1.5

What was understood: `pre_llm_call` told the model to call `edinet_search_filings` for one past day and named `2024-06-25`. It did not name `edinet_financials`. On a small model the search arguments were that date, and the reply said the sales could be confirmed. The README said the five-year span is the same width EDINET DB's indicator reference uses. The recorded Toyota file `tests/fixtures/csv_toyota_ifrs_S100TR7I.zip` (https://disclosure2.edinet-fsa.go.jp/WZEK0040.aspx?S100TR7I, period ended 2024-03-31) also has a filing-company series on the context whose id is exactly that period's Duration or Instant plus `_NonConsolidatedMember`. CurrentYear revenue there is 17,575,593,000,000 円. Consolidated CurrentYear revenue on the same file is 45,095,325,000,000 円. A missing key was checked after the date, so a guessed date was reported as a date error. Pytest on this commit: 104 passed, 6 skipped.

What changed: `edinet_financials` returns `filing_company` for the same six lines and the same five periods. A missing line stays `found: false` and is not copied from the other series and is not written as 0. When both CurrentYear revenues are present and differ, `source.note` states both amounts and says not to add them. `pre_llm_call` does not name a file date. Without a key it says to call `edinet_search_filings` with the company name only and to copy `next_step`. With a key it says to call `edinet_financials`. A missing key is returned before a date or document-id check. Filing-company net income uses `jppfs_cor:ProfitLoss` on that series only (4,399,855,000,000 円 on the Toyota file). Operating cash flow is absent from that series on the same file.

## 0.1.4

What was understood: `periods` already returns the six consolidated lines for CurrentYear and Prior1Year through Prior4Year. The README Metrics section and the Japanese section still said a headline row is kept only for `CurrentYearDuration` or `CurrentYearInstant`. The catalog sentence said `found: false` means the figure is missing from the current-year rows, and a later sentence said a missing line in any of five periods is `found: false`. On a small model, the skill body was not opened, `edinet_financials` was not called, and the reply said the five periods had been fetched.

What changed: the Metrics section, the Japanese section, and the skill now name the same five periods. `found: false` means that line is absent from that period's consolidated rows. The non-consolidated member is not substituted and 0 is not written. `pre_llm_call` tells the model to call `edinet_search_filings` for one past day and to copy `next_step` instead of writing yen. The first section states that five years is the filing-table span EDINET DB's indicator reference also uses, and that this tool does not fall back to standalone when a consolidated line is absent.

## 0.1.3

What was understood: one yuho CSV already carries the consolidated six lines for the current period and up to four prior periods (`Prior1Year` through `Prior4Year`). The extractor read only `CurrentYearDuration` and `CurrentYearInstant`. On the recorded Toyota zip `csv_toyota_ifrs_S100TR7I.zip` (filing page https://disclosure2.edinet-fsa.go.jp/WZEK0040.aspx?S100TR7I), consolidated operating revenues are 45,095,325,000,000 yen for CurrentYear, 37,154,298,000,000 yen for Prior1Year, and 29,866,547,000,000 yen for Prior4Year, unit 円. A missing prior line was not representable, so a caller could not tell it apart from a line that was never looked up.

What changed: `edinet_financials` now returns `periods` (five consolidated periods) and keeps `metrics` as the current period. A line that is not in that period's consolidated context is `found: false` and `value: null`. 個別 and member contexts stay ignored. The missing-key reply names the six consolidated lines that stay empty until a free `EDINET_API_KEY` is set. The skill tells the model to keep this series apart from gBizINFO's non-consolidated series and to copy the unit string into a chart.

## 0.1.2

The first sentence Tool Search shows now uses the words a person would ask with. A sentence that starts by describing the mechanism is not found, and the model falls through to the built-in web_search.

Adds the `edinet-filings` skill. It tells the model to find the EDINET tools with tool_search before web_search. It does not set requires_toolsets, so it stays in the model's skill list when Tool Search is left at its default, which hides this plugin's tools.

Date separators, fullwidth digits in a date, a fullwidth securities code, and a fullwidth document id are accepted. A week date, a future date, and a document id of the wrong shape are refused with a fix and an example, and the caller's value is not copied into the message. Error results add `ok: false` and `error_type`.

## 0.1.1

`EDINET_API_KEY` was read from `os.environ` on each call, including when error text was scrubbed. One Hermes process can serve more than one profile, so that read can use another profile's API key. Each call now reads it with `agent.secret_scope.get_secret`. Each profile needs `EDINET_API_KEY` in that profile's `.env`. A profile without the key cannot call EDINET, even when another profile has it.

An HTTP proxy set in the environment is not used for EDINET requests. A machine that can reach the network only through a proxy can no longer reach EDINET.

`requires_hermes` is `>=0.21.6`, the same floor as the catalog entry already on main. This version was tested on Hermes 0.21.4 and later. Hermes 0.21.4 and 0.21.5 will not load version 0.1.1. Version 0.1.0's manifest allowed `>=0.21.4`.
