"""Which of the six books this turn belongs to.

The same file is copied into each book. It does not import another plugin.
A katakana-kanji shape is not a company. The signals are a 13-digit corporate
number, a legal-form word, a name in the recorded registry table, or a tool
from these six books called in the previous turn. A paragraph is added only
for the book that owns the matching signal.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, FrozenSet

# Registered names with the legal-form word removed. Sources are the recorded
# fixtures, not a character pattern:
#   トヨタ自動車株式会社  gBizINFO hojin 1180301018771 and EDINET list 2024-06-25
#   大栄精工株式会社        gBizINFO hojin 1010801006516
#   株式会社グローバルプロダクトプランニング  gBizINFO hojin 5011001043924
#   キヤノン株式会社        EDINET list 2024-03-28
#   任天堂株式会社          EDINET list 2024-06-28
#   ソニーグループ株式会社  EDINET list 2024-06-25
# ソニー alone is not ソニーグループ. 日産自動車 is not in these fixtures.
_REGISTRY_NAMES = (
    "グローバルプロダクトプランニング",
    "ソニーグループ",
    "トヨタ自動車",
    "大栄精工",
    "キヤノン",
    "任天堂",
)

_LEGAL = re.compile(
    r"株式会社|有限会社|合同会社|合名会社|合資会社|相互会社|"
    r"特定非営利活動法人|一般社団法人|一般財団法人|公益社団法人|公益財団法人|"
    r"\(株\)|\(有\)|\(同\)"
)
_CORP_NO = re.compile(r"(?<!\d)\d{13}(?!\d)")
_CORP_WORD = re.compile(r"法人番号")
# EDINET as a word. edinet_search_filings is a tool name, not this word.
_FILING = re.compile(
    r"有報|有価証券|連結|提出会社|(?<![A-Za-z0-9_])EDINET(?![A-Za-z0-9_])|"
    r"(?<![A-Za-z0-9_])securities(?![A-Za-z0-9_])|"
    r"(?<![A-Za-z0-9_])consolidated(?![A-Za-z0-9_])",
    re.I,
)
_ADDRESS = re.compile(r"所在地|住所|都道府県|丁目|番地")
_PREFS = (
    "神奈川県|和歌山県|鹿児島県|北海道|青森県|岩手県|宮城県|秋田県|山形県|福島県|"
    "茨城県|栃木県|群馬県|埼玉県|千葉県|東京都|新潟県|富山県|石川県|福井県|"
    "山梨県|長野県|岐阜県|静岡県|愛知県|三重県|滋賀県|京都府|大阪府|兵庫県|"
    "奈良県|鳥取県|島根県|岡山県|広島県|山口県|徳島県|香川県|愛媛県|高知県|"
    "福岡県|佐賀県|長崎県|熊本県|大分県|宮崎県|沖縄県"
)
_PREF = re.compile(_PREFS)
_SUBSIDY = re.compile(r"補助金|助成金|(?<![A-Za-z0-9_])jGrants(?![A-Za-z0-9_])|募集", re.I)
# 条 inside 条件 is not an article. A tool name is not a statute word.
_LAW = re.compile(r"法令|法律|条文|e-Gov|第\d+条|(?<![0-9条])条(?!件)", re.I)
_CHART = re.compile(r"グラフ|チャート|(?<![A-Za-z0-9_])chart(?![A-Za-z0-9_])|図", re.I)

_TOOL_DOMAIN = {
    "jp_corp_search": "company",
    "jp_corp_profile": "company",
    "edinet_search_filings": "filing",
    "edinet_financials": "filing",
    "edinet_save_document": "filing",
    "address_split": "address",
    "subsidies_search": "subsidy",
    "subsidies_detail": "subsidy",
    "subsidies_watch_setup": "subsidy",
    "egov_law_search": "statute",
    "egov_law_article": "statute",
    "egov_law_revisions": "statute",
    "chart_render": "chart",
    "chart_validate": "chart",
    "chart_types": "chart",
}


def message_text(user_message: Any) -> str:
    """NFKC text of the public user_message string or content parts."""
    if isinstance(user_message, str):
        text = user_message
    elif isinstance(user_message, list):
        parts = []
        for item in user_message:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                for key in ("text", "content"):
                    value = item.get(key)
                    if isinstance(value, str):
                        parts.append(value)
                        break
        text = "\n".join(parts)
    else:
        text = ""
    return unicodedata.normalize("NFKC", text)


def _call_name(call: Any) -> str:
    if not isinstance(call, dict):
        return ""
    function = call.get("function")
    if isinstance(function, dict):
        name = function.get("name")
        if isinstance(name, str) and name:
            return name
    name = call.get("name")
    if isinstance(name, str):
        return name
    return ""


def previous_turn_tools(conversation_history: Any) -> FrozenSet[str]:
    """Tool names called in the turn before the current user message.

    pre_llm_call receives the public conversation_history after the current
    user message has been appended. Tool names written in the user text are
    not a call record.
    """
    if not isinstance(conversation_history, list):
        return frozenset()
    rows = [item for item in conversation_history if isinstance(item, dict)]
    if rows and rows[-1].get("role") == "user":
        rows = rows[:-1]
    names = set()
    for msg in reversed(rows):
        role = msg.get("role")
        if role == "user":
            break
        if role == "assistant":
            for call in msg.get("tool_calls") or []:
                name = _call_name(call)
                if name:
                    names.add(name)
        elif role == "tool":
            name = msg.get("name")
            if isinstance(name, str) and name:
                names.add(name)
    return frozenset(names)


def domains(user_message: Any, conversation_history: Any = None) -> FrozenSet[str]:
    """Books that own this turn. Empty when none of the strong signals match."""
    text = message_text(user_message)
    found = set()
    if _CORP_NO.search(text) or _LEGAL.search(text) or _CORP_WORD.search(text):
        found.add("company")
    for name in _REGISTRY_NAMES:
        if name in text:
            found.add("company")
            break
    if _FILING.search(text):
        found.add("filing")
    if _ADDRESS.search(text) or _PREF.search(text):
        found.add("address")
    if _SUBSIDY.search(text):
        found.add("subsidy")
    if _LAW.search(text):
        found.add("statute")
    if _CHART.search(text):
        found.add("chart")
    for tool in previous_turn_tools(conversation_history):
        domain = _TOOL_DOMAIN.get(tool)
        if domain:
            found.add(domain)
    return frozenset(found)


def owns(user_message: Any, conversation_history: Any, book: str) -> bool:
    return book in domains(user_message, conversation_history)
