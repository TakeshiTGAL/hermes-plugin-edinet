---
name: edinet-filings
description: "Japan EDINET filings and securities reports. Before web_search, use tool_search to find the EDINET tools and tool_call edinet_financials when a doc_id is known. Do not call tool_describe. If next_step is present, copy it. Do not write yen the tool did not return. Do not write successfully retrieved, verified, or obtained using. That ban is the same as 取得しました. periods and filing_company are separate. A missing line is found:false, not 0."
---

For this kind of request, use tool_search to find this plugin's tools before web_search, then call them with tool_call. Write tool_search queries in English (for example: "EDINET filings").

Tools: `edinet_search_filings`, `edinet_financials`, `edinet_save_document`.

Do not write "successfully retrieved", "verified", or "obtained using". That ban is the same as 取得しました.

When the user asks for a Japanese company's sales, financials, or one company sheet, call `edinet_financials` on the yuho `doc_id`. `periods` is the consolidated series: CurrentYear plus Prior1Year through Prior4Year (at most five). `filing_company` is the filing company for the same six lines and the same periods. `metrics` is the current consolidated period. A line with `found: false` is absent from that series. Do not copy it from the other series. Do not write 0. Copy each line's `unit` and `source.filing_page` into the reply. Do not invent a file date.

Do not add these amounts to `jp_corp_profile` section `finance`. That section is gBizINFO's non-consolidated series and can be a different fiscal year. Put the series in separate blocks, each with its own URL and unit.

For a chart, call `chart_render` with that same `unit` string (for these rows, `円`). Do not rewrite it as 百万円 or 億円. A different unit string is blocked and no image is returned.

If the tool says `EDINET_API_KEY` is not set, say the consolidated six lines stay empty until the free key in `next_step` is set. Do not invent the figures. Non-consolidated rows are a different key (`GBIZINFO_API_TOKEN`) and stay empty for the same reason when that token is unset.
