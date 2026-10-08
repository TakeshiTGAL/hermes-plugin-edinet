# jp-edinet: Japanese corporate filings for Hermes Agent

EDINET disclosures in [Hermes Agent](https://hermes-agent.nousresearch.com), built on the EDINET API v2 (Electronic Disclosure for Investors' NETwork / 金融庁). Search for 有価証券報告書 and related filings by company, pull headline financials from the CSV ZIP with units and accounting standard, and save PDF/ZIP files into Hermes' cache.

```
You:    What were Toyota's FY2024 operating revenues and total assets in the yuho?
Hermes: 営業収益 (operating revenues) 45,095,325,000,000 円, total assets 90,114,296,000,000 円 (IFRS).
        出典：EDINET閲覧（提出）サイト（https://disclosure2.edinet-fsa.go.jp/WZEK0040.aspx?S100TR7I）、
        PDL1.0（https://www.digital.go.jp/resources/open_data/public_data_license_v1.0）
        EDINET閲覧（提出）サイト（https://disclosure2.edinet-fsa.go.jp/WZEK0040.aspx?S100TR7I）より抜粋して作成
```

Those yen figures match the yuho PDF lines「営業収益」/「総資産」in the key-metrics table (**PDF shows 百万円**; the tool returns **円**, so multiply the PDF line by 1,000,000). The tool field is `revenue` (headline top-line; here IFRS 営業収益 / operating revenues, not always Japanese 売上高) / `total_assets` with `unit` and `element_id`. Company-extension tags are matched via a known alias list only — if a headline tag is missing from that list, the metric is `found: false` (not invented). When CSV `label` (項目名) is blank, the tool fills a short fallback from the matched `element_id` (e.g. Toyota's company-extension revenue tag →「営業収益（IFRS）」).

[日本語の説明は下にあります](#日本語)

## Install

1. Get a free EDINET API key ([how](#getting-an-api-key)).
2. Install and enable the plugin. Hermes asks for `EDINET_API_KEY` and saves it to the Hermes home `.env`:
   ```bash
   hermes plugins install TakeshiTGAL/hermes-plugin-edinet --enable
   ```
   Once listed in the Hermes plugin catalog, `hermes plugins install jp-edinet --enable` works too. For a local clone: `hermes plugins install "file://$PWD" --enable`.
3. Start a new Hermes session and ask about a listed company's filing.

Works with Hermes Agent **0.21.4** or later (`manifest_version: 2`).

## Tools

| Tool | What it does |
|---|---|
| `edinet_search_filings` | Search by `company_name` (substring; prefer `sec_code` / `edinet_code` for a single issuer) / `sec_code` / `edinet_code` / `corporate_number` and a file-date range. Optional `doc_types`: `yuho`, `annual`, `quarterly`, `semiannual`, `extraordinary`, `amendment`. `amendment` does not include 135 (確認書) or 136 (訂正確認書). A list **404** (`not_found`, outside retention) is skipped. A list **400** (envelope or raw HTTP) fails the whole range. |
| `edinet_financials` | Given an 8-character `doc_id` (for example `S100TR7I`), fetch type=5 CSV and return revenue, operating income, net income, total assets, net assets/equity, operating cash flow — each with unit and `accounting_standard`. A shorter or longer id is rejected before any request. |
| `edinet_save_document` | Save type=2 PDF, type=1 ZIP, or type=5 CSV ZIP under Hermes' writable cache; returns the absolute path. No summarization. |

### File dates and cache honesty

The list API is **by file date only** (`documents.json?date=YYYY-MM-DD&type=2`). `date_from` and `date_to` must be `YYYY-MM-DD`. Week dates (`2024-W26-2`) and compact dates (`20240625`) are rejected before any request, not converted to another day. Past dates are **not** immutable: withdrawals, disclosure status, staff edits and viewing-period expiry can change a day's list. The plugin caches each day under `<HERMES_HOME>/plugin-data/jp-edinet/lists/<api-key-fingerprint>/YYYY-MM-DD.json` (the Hermes home of the active profile; fingerprint is a short SHA of the key, never the key itself) with a TTL — **today (JST) ≤15 minutes**, **past dates ≤24 hours** — not forever. `metadata.resultset.count` must equal the number of rows or that day fails and is not cached. A day's list that returns **404** `not_found` (outside retention) is **skipped** so one unavailable day does not fail the whole range (`cache.days_list_unavailable`). A **400** fails the whole date range and is not skipped. If a file date that previously had rows then returns 0, the reply warns until a non-zero list comes back. Saved PDF/ZIP under `<HERMES_HOME>/plugin-data/jp-edinet/docs/<api-key-fingerprint>/{docID}_typeN…` have **no TTL** (save always writes a fresh download and never deletes it). `saved_at` is Japan time (JST), the same zone as file dates. `edinet_financials` keeps a **separate** cache at `…/docs/<fingerprint>/financials/{docID}_type5.zip` for **≤24 hours**, then re-fetches, and warns when serving from that cache. A different or invalid key cannot read another key's cached files. If `plugins.plugin_storage.plugin_data_dir` cannot be loaded, cache reads and writes are refused. Nothing is written to `~/.hermes` as a fallback. Live EDINET GETs share a process-wide ≥1s gap under a 180s per-call budget; retries of HTTP 429, 5xx, and network errors stop at 2. `max_calendar_days` (default/absolute max 31) limits span size but does not promise a cold full-span search always finishes. If a search range exceeds that cap, the tool **errors with `next_step`** and never silently truncates. `limit` outside 1–200 is rejected (0 does not become 1). A row limit that lands on the last match of the last day does not set `truncated`.

### Metrics

CSV (type=5) ZIPs contain utf-16 TSV. A headline row is kept only when the context is `CurrentYearDuration` or `CurrentYearInstant` (no dimension member) and the `連結・個別` column is `連結` or `その他`. `その他` is EDINET's label when the context has no consolidation axis; that is where consolidated headlines usually sit. `個別`, segment members, and equity-component members are ignored. The plugin maps several taxonomy element IDs per metric (Japan GAAP / IFRS / US GAAP). If a line is absent, the metric is returned as `found: false` with a clear message — **never invent 0**. `found: false` means that alias was absent from those consolidated rows. It is not proof the filing has no figure. If a cached search list shows `csvFlag` 0, `edinet_financials` says this document has no CSV (`no_csv`) and does not call EDINET. That result is not `not_found`.

### Working with jp-corporate and jp-charts

In prose only (no hard dependency): resolve a legal entity with **jp-corporate** (法人番号), pass that number into `edinet_search_filings`, then chart the returned metrics with **jp-charts**.

## Getting an API key

1. Open the FSA EDINET API materials index (WEEK0060): <https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/WEEK0060.html>
2. Follow the linked API specification PDF for account creation and key issue.
3. Paste it when `hermes plugins install` asks for `EDINET_API_KEY`, or add `EDINET_API_KEY=<your key>` to the Hermes home `.env`.

## Citation and terms

EDINET terms require source citation ([WZEK0030](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/WZEK0030.html) §1.1). That sample asks for「当該ページのURL」. When a docID is known, financials and save cite the viewing-site filing page `https://disclosure2.edinet-fsa.go.jp/WZEK0040.aspx?{docID}` plus PDL1.0, and return a separate `excerpt_note` for that same page. Search attaches `filing_page` on each hit and also returns overall `source.citation` / `source.excerpt_note` — that page when there is exactly one hit, otherwise the viewing-site **portal root** plus PDL1.0 (zero or multiple hits). `net_assets` / `equity` follow the matched CSV tag: Japan GAAP `NetAssetsSummary…` is usually 純資産合計 (may include NCI); IFRS/US GAAP preferred tags are usually equity attributable to owners — see each metric's `meaning`.

Example (financials):

`出典：EDINET閲覧（提出）サイト（https://disclosure2.edinet-fsa.go.jp/WZEK0040.aspx?S100TR7I）、PDL1.0（https://www.digital.go.jp/resources/open_data/public_data_license_v1.0）`

`excerpt_note` uses the same filing page and is not glued onto the 出典 line.

API terms forbid abusive burst traffic. The client talks only to `api.edinet-fsa.go.jp` over HTTPS, follows same-host redirects only, times out each request, enforces a process-wide ≥1s gap between live GETs inside one process (list or document, including parallel tool calls in that process; separate processes do not share the gap), and retries transient failures (HTTP 429/5xx and network errors) at most twice with ≥1s delay. The key is sent as `Subscription-Key` in the query string (EDINET's design); errors and tool JSON never echo the key or full request URLs.

## Security and privacy

- Tools are read-only against EDINET; saved files go only under `<HERMES_HOME>/plugin-data/jp-edinet/` (per Hermes profile).
- HTTPS to `api.edinet-fsa.go.jp` only. The API key is sent in the URL as `Subscription-Key`.
- Python standard library only; no `python_dependencies`.
- No telemetry.

## Disclosure

There is no daily cap beyond the 180-second call budget, the ≥1 second gap, and at most 2 retries. The agent can call `edinet_search_filings`, `edinet_financials`, and `edinet_save_document` itself once the plugin is enabled. This plugin starts no child process and is not a sandbox. It registers no cron. A cron a person creates still runs one agent turn per firing, and removing the plugin does not delete that cron; remove the cron in Hermes. Cache under `<HERMES_HOME>/plugin-data/jp-edinet` (per Hermes profile) stays after uninstall. Delete that directory to remove lists, saved files, and the financials ZIP cache. The edinet toolset is enabled on every platform by default, including gateways. Anyone a gateway admits can call these tools with your EDINET key. For example, while `CHATWORK_ALLOWED_USERS` is unset, everyone in a room listed in `CHATWORK_ROOMS` can talk to the bot and therefore call these tools. Turn the toolset off per platform with `hermes tools`. What is stored on disk is the list JSON, saved PDF/ZIP bytes, and a short fingerprint of the API key. The key itself is not stored. The Hermes session still has the tool text. Writes use the private module `agent.file_safety`. The cache directory comes from `plugins.plugin_storage`. If either cannot be loaded, the write check denies the path, or the write check raises, the tool refuses (`access_denied`) and does not call EDINET. Search sends EDINET the filing date. A download sends the document id and its type. Company name, securities code, EDINET code, and corporate number are filtered on your machine. A past day's filing list can change. `tests/` is shipped and `register()` does not load it. A 4-digit `sec_code` matches any EDINET 5-digit code with that prefix, including a fifth digit other than 0. `7203` matches `72030`. The 2024-06-25 example list showed `72030` only. `corporate_number` must be 13 digits. `doc_types` names are the English words above (`yuho`, not 有報). After an 8-character id is accepted, a document 400 asks you to check that id from search and the type. It does not say the id should be 8 characters.

## Not checked on the live API

These were not run against the live EDINET API. Retry counts, size caps, and TTL formulas were checked in code and with fake responses:

- A cold search of exactly 31 calendar days, through to the end. Spans of 32 days or more are rejected before any request.
- A sudden drop in list counts, a redirect to another host, waiting out HTTP 429, and ZIP size caps, as live responses.
- Waiting out the 15-minute TTL on today's list in real time.
- A fresh download of a type=1 filing ZIP. Magic bytes and size caps are offline.
- Opening the viewing-site HTML and clicking figures. Toyota `S100TR7I` key metrics were compared with the yuho PDF key-metrics table (百万円 times 1,000,000 equals 円).
- A real call from a gateway room. This plugin has no inbound surface. Who can call the tools is the Hermes default toolset plus the disclosure above.
- Calling a tool once inside a `plugins.isolation: host` child process, beyond the isolation line that validate prints.
- The uninstall action itself. The code does not delete the cache. Delete `<HERMES_HOME>/plugin-data/jp-edinet` to remove it.
- The body shape of live HTTP 429 and 5xx. The client retries when the status is 429 or 5xx.

## Development

Install the plugin with `hermes plugins install` (see above). The offline tests in this checkout are not part of that install:

```bash
python -m pytest -q
```

Tests replay fixtures in `tests/fixtures/` (see that folder's README). Before publishing a pin: `hermes plugins validate . --install-deps`. On Hermes 0.21.4 (tag v2026.9.21) that prints `Validation passed` and `security scan — safe`. `no core override` is printed by current main, not by 0.21.4.

## License

MIT — see `LICENSE` and `NOTICE`.

---

## 日本語

**jp-edinet** は、金融庁の EDINET を活用した Hermes Agent 向けプラグインです（EDINET API v2）。会社名・証券コード・EDINET コード・法人番号と提出日（ファイル日付）で書類を探し、CSV（type=5）からトップライン（売上高／営業収益／Revenues 等）・利益・資産などの主要指標を単位・会計基準付きで取り出し、PDF/ZIP を Hermes のキャッシュに保存します。
### インストール

1. EDINET API キーを発行する（[API 資料一覧 WEEK0060](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/WEEK0060.html) から仕様書 PDF の手順へ）。API キーはリクエスト URL の `Subscription-Key` として送られます（エラーと JSON には出しません）。
2. `hermes plugins install TakeshiTGAL/hermes-plugin-edinet --enable`（カタログ掲載後は `jp-edinet` でも可）。
3. セッションを開き直して質問する。

### ツール

- `edinet_search_filings` … 書類検索（日付ごと取得＋TTL キャッシュ。過去日も変わり得るので永久キャッシュしない）。保存先はプロファイルごとの `<HERMES_HOME>/plugin-data/jp-edinet`。edinet の道具は既定でゲートウェイを含む全部の面で有効です。`CHATWORK_ALLOWED_USERS` が無いとき、`CHATWORK_ROOMS` に書いた部屋の全員が、あなたの EDINET キーでこの道具を呼べます。面ごとに止めるには `hermes tools`。
- `edinet_financials` … 主要指標の抽出（無い項目は `found: false`。0 を埋めない）。採る行は、コンテキストが `CurrentYearDuration` または `CurrentYearInstant` で、`連結・個別` が `連結` または `その他` の行だけ。セグメントや純資産の内訳（メンバー付き）と `個別` は採らない。キャッシュした一覧の `csvFlag` が 0 の書類は「この書類には CSV が無い」（`no_csv`）で、`not_found` ではない。EDINET は呼ばない。書類 ID は 8 文字（例 `S100TR7I`）。短い・長い ID は API の前に拒否する。`amendment` に 135（確認書）と 136（訂正確認書）は含めない。
- `edinet_save_document` … PDF/ZIP 保存（要約しない）

### 開示

日ごとの呼び出し上限は、1呼び出し 180 秒、同一プロセス内のライブ GET の 1 秒以上の間隔、再試行は最大 2 回、以外にありません。エージェントは有効にしたあと、3つの道具を自分で呼べます。このプラグインは cron を登録しません。人が作った cron はエージェントの 1 ターンとして動き、プラグインを消しても残ります。消すには Hermes 側でその cron を消します。子プロセスは起動せず、砂場ではありません。`tests/` は同梱しますが `register()` は読みません。非公開の部品は `agent.file_safety` と `plugins.plugin_storage` です。読めないとき、書き込みを拒否されたとき、またはその確認が例外を出したときは、`access_denied` で EDINET を呼ばずに拒否します。一覧 API に送るのは提出日で、書類の取得に送るのは書類 ID と type です。会社名・証券コード・EDINET コード・法人番号の絞り込みは手元で行います。過去の提出日の一覧は変わり得ます。記録するのは一覧 JSON、保存した PDF/ZIP、鍵の指紋です。鍵そのものは保存しません。セッションには道具の文が残ります。キャッシュは `<HERMES_HOME>/plugin-data/jp-edinet` に残り、消すにはそのディレクトリを消します。

`sec_code` の 4 桁は、EDINET の 5 桁がその 4 桁で始まるときに合います。5 桁目が 0 以外でも前方一致します。`7203` は `72030` に合います。2024-06-25 の例で見えたのは `72030` だけです。`found: false` は、その別名が連結の CurrentYear に無かったという意味で、書類に数字が無いことの証明ではありません。8 文字として通ったあとの書類の 400 は、検索で得た ID と type を確かめる案内で、桁の直しとは書きません。

実 API では次を行っていません。31 日ちょうどのコールド検索を最後まで取ること。件数の急落、他ホストへの 302、429 の待ち、ZIP の上限を実応答で起こすこと。今日の一覧の 15 分 TTL を実時間で待つこと。type=1 の提出 ZIP を新規に落とすこと。閲覧サイトの HTML を開いて数字をクリックすること。ゲートウェイの部屋からの実際の呼び出し。`plugins.isolation: host` の子プロセスで道具を 1 回呼ぶこと。アンインストール操作そのもの。429 と 5xx の本文の形を実 API で採取すること。再試行、上限、TTL の式はコードと偽物で見ています。トヨタ `S100TR7I` の主要指標は、有報 PDF の主要指標表（百万円）に 1,000,000 を掛けた円と照合しています。

### 出典

利用時は EDINET の利用規約（WZEK0030 §1.1）に沿い、出典を明示してください。検索では各件に提出書類ページ（`WZEK0040.aspx?{docID}`）を付け、ヒットがちょうど1件のときだけ全体出典もそのページ＋PDL1.0 にし、0件または複数件のときはポータル＋PDL1.0 です。API への過負荷な連続アクセスは行わないでください。
jp-corporate で法人を特定し、jp-charts でグラフ化する、といった組み合わせは README 上の利用例です（コード依存はありません）。
