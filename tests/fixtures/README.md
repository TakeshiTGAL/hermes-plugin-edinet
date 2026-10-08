# Test fixtures (offline)

Trimmed / recorded responses from the **EDINET API v2** (`api.edinet-fsa.go.jp`).
No API keys are stored here. Values are from public filings; cite EDINET when reusing figures.

| File | Source | Retrieved / filing context |
|---|---|---|
| `list_2024-06-25.json` | `GET /api/v2/documents.json?date=2024-06-25&type=2` (subset: Toyota S100TR7I IFRS, Sony, related) | 2024 yuho filing day |
| `list_2024-06-28.json` | List for `2024-06-28` (Nintendo S100TMMG Japan GAAP) | 2024 yuho filing day |
| `list_2024-03-28.json` | List for `2024-03-28` (Canon S100T58N US GAAP) | 2024 yuho filing day |
| `csv_toyota_ifrs_S100TR7I.zip` | `GET /api/v2/documents/S100TR7I?type=5` | Toyota Motor, period ended 2024-03-31, IFRS |
| `csv_nintendo_jpgaap_S100TMMG.zip` | `GET /api/v2/documents/S100TMMG?type=5` | Nintendo, period ended 2024-03-31, Japan GAAP |
| `csv_canon_usgaap_S100T58N.zip` | `GET /api/v2/documents/S100T58N?type=5` | Canon, period ended 2023-12-31, US GAAP |
| `sample.pdf` | Tiny placeholder PDF for `edinet_save_document` tests | synthetic |

CSV ZIPs contain utf-16 TSV with columns: 要素ID, 項目名, コンテキストID, 相対年度, 連結・個別, 期間・時点, ユニットID, 単位, 値.

出典：EDINET閲覧（提出）サイト（https://disclosure2.edinet-fsa.go.jp/WZEK0040.aspx?S100TR7I）、PDL1.0（https://www.digital.go.jp/resources/open_data/public_data_license_v1.0）

出典：EDINET閲覧（提出）サイト（https://disclosure2.edinet-fsa.go.jp/WZEK0040.aspx?S100TMMG）、PDL1.0（https://www.digital.go.jp/resources/open_data/public_data_license_v1.0）

出典：EDINET閲覧（提出）サイト（https://disclosure2.edinet-fsa.go.jp/WZEK0040.aspx?S100T58N）、PDL1.0（https://www.digital.go.jp/resources/open_data/public_data_license_v1.0）

出典：EDINET閲覧（提出）サイト（https://disclosure2.edinet-fsa.go.jp/WZEK0040.aspx?S100TS7P）、PDL1.0（https://www.digital.go.jp/resources/open_data/public_data_license_v1.0）
