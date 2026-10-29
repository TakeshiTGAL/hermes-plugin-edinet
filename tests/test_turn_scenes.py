"""Scene table for pre_llm_call. A katakana-kanji shape is not a company."""

OWN = "filing"

# Sentences that are not about a company, a filing, an address, a subsidy, a statute, or a chart.
OFF = (
    "Pythonでフィボナッチ数列を計算する関数を書いて。",
    "グラタン皿でチーズグラタンを作る手順を教えて。",
    "明日の東京の天気は？傘は要る？",
    "hermes の設定はどこで見る？",
    "来週の火曜の午前に歯医者の予定を入れて。",
    "12掛ける8はいくつ？",
    "京都へ二泊三日で行くときの荷物を教えて。",
    "コーヒーの入れ方を短く教えて。",
    "ソニーのヘッドホンはどれが静か？",
    "親子丼のレシピを家庭用で教えて。",
    "リストを逆順に並べ替えるコードを書いて。",
    "週末に映画を見に行くなら何がいい？",
    "洗濯機の脱水がうるさいときの点検項目は？",
    "英語で自己紹介する文を3つ書いて。",
    "明日の朝6時に起こして。",
    "この写真の明るさを上げる手順は？",
    "自転車のタイヤの空気圧はどれくらい？",
    "アルゴリズムとは何かを短く説明して。",
    "ラーメンを家で作る手順を教えて。",
    "2の10乗を計算して。",
    "来月の旅行は大阪にしたい。",
    "キーボードの配列をUSにしたときの記号の位置を教えて。",
    "chart_render と jp_corp_search の違いを説明して。",
    "edinet_search_filings の引数名だけ教えて。",
)

# Company sentences. Only the book that owns the signal returns a paragraph.
ON = (
    ("法人番号1180301018771の資本金を教えて。", frozenset({"company"})),
    ("株式会社の従業員数を教えて。", frozenset({"company"})),
    ("トヨタ自動車について教えて。", frozenset({"company"})),
    ("㈱トヨタ自動車の登記は開いていますか。", frozenset({"company"})),
    ("ﾄﾖﾀ自動車の概要を教えて。", frozenset({"company"})),
    ("有報の連結を5期分ください。", frozenset({"filing"})),
    ("提出会社の売上を有価証券報告書で見て。", frozenset({"filing"})),
    ("登記の所在地を都道府県に分けて。", frozenset({"address"})),
    ("愛知県豊田市トヨタ町1番地を分解して。", frozenset({"address"})),
    ("今出ている補助金の上限を教えて。", frozenset({"subsidy"})),
    ("jGrantsの助成金を検索して。", frozenset({"subsidy"})),
    ("労働基準法第32条を公式のページで開いて。", frozenset({"statute"})),
    ("e-Govでこの法令の施行日を教えて。", frozenset({"statute"})),
    ("売上の推移を図にして。", frozenset({"chart"})),
    ("棒グラフで比較して。", frozenset({"chart"})),
    ("トヨタ自動車の有報の売上を図にして。", frozenset({"company", "filing", "chart"})),
)

PREVIOUS = (
    ("jp_corp_search", "company"),
    ("edinet_financials", "filing"),
    ("address_split", "address"),
    ("subsidies_search", "subsidy"),
    ("egov_law_article", "statute"),
    ("chart_render", "chart"),
    ("write_file", ""),
)


def _history(tool_name):
    return [
        {"role": "user", "content": "先の依頼"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": tool_name, "arguments": "{}"}}],
        },
        {"role": "tool", "name": tool_name, "content": "{}"},
        {"role": "user", "content": "続きを頼む。"},
    ]


def _nudge(plugin, text, history=None):
    return plugin._nudge_edinet(user_message=text, conversation_history=history)


def test_off_scenes_add_no_paragraph(plugin):
    assert len(OFF) >= 20
    for text in OFF:
        assert plugin.turn_scope.domains(text) == frozenset(), text
        assert _nudge(plugin, text) is None, text


def test_on_scenes_add_only_the_owning_book(plugin):
    assert len(ON) >= 10
    for text, expected in ON:
        assert plugin.turn_scope.domains(text) == expected, text
        out = _nudge(plugin, text)
        if OWN in expected:
            assert isinstance(out, dict) and out.get("context"), text
        else:
            assert out is None, text


def test_previous_turn_tool_selects_one_book(plugin):
    for tool_name, book in PREVIOUS:
        history = _history(tool_name)
        expected = frozenset({book}) if book else frozenset()
        assert plugin.turn_scope.domains("続きを頼む。", history) == expected, tool_name
        out = _nudge(plugin, "続きを頼む。", history)
        if book == OWN:
            assert isinstance(out, dict) and out.get("context"), tool_name
        else:
            assert out is None, tool_name
