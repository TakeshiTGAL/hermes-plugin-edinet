"""jp-edinet: EDINET filings and headline financials for Hermes Agent.

Three tools: search filings by company and file date, extract key metrics from
the CSV ZIP, and save PDF/ZIP documents into Hermes' cache.
"""

# Hermes imports this directory as a package. pytest's collector also imports this
# file on its own (the repo root is the package), where relative imports cannot work.
if __package__:
    from . import cache, citation, client, metrics, save, schemas, settings, tools, turn_scope  # noqa: F401
else:
    import turn_scope  # noqa: F401

_SKILL_DESCRIPTION = (
    "Japan EDINET filings and securities reports. "
    "Before web_search, use tool_search to find the EDINET tools and tool_call edinet_financials "
    "when a doc_id is known. "
    "Do not call tool_describe. If next_step is present, copy it. "
    "Do not write yen the tool did not return. "
    "Do not write successfully retrieved, verified, or obtained using. "
    "That ban is the same as 取得しました. "
    "periods and filing_company are separate. A missing line is found:false, not 0."
)


def _edinet_turn(user_message, conversation_history=None) -> bool:
    """A filing question, or an EDINET tool called in the previous turn."""
    return turn_scope.owns(user_message, conversation_history, "filing")


def _nudge_edinet(user_message="", conversation_history=None, **_kwargs):
    if not _edinet_turn(user_message, conversation_history):
        return None
    try:
        from . import client as _client
    except ImportError:
        _client = None
    key_set = _client.key_is_set() if _client is not None else None
    if key_set is None:
        return {"context": (
            "The EDINET secret helper could not be read. Do not say EDINET_API_KEY is unset. "
            "Do not invent a file date. Do not write yen, 取得しました, or 確認できます. "
            "Do not write successfully retrieved, verified, or obtained using. "
            "That ban is the same as 取得しました."
        )}
    if key_set is False:
        steps = _client.API_KEY_STEPS if _client is not None else ""
        return {"context": (
            "EDINET_API_KEY is not set. Call edinet_search_filings once with the company name only. "
            "Do not add date_from or date_to. Do not invent a file date. Do not call tool_describe. "
            "Copy this next_step into the reply. It is the free key that fills the consolidated periods "
            "and the filing_company series, each with its unit and the EDINET filing URL:\n"
            f"{steps}\n"
            "Do not write yen, 取得しました, or 確認できます. "
            "Do not write successfully retrieved, verified, or obtained using. "
            "That ban is the same as 取得しました."
        )}
    return {"context": (
        "If a tool result already contains a doc_id, call edinet_financials with that id. "
        "If no doc_id has been returned yet, the next step is edinet_search_filings with the company name "
        "and only the file dates the user wrote. If the user wrote no file date, say the document id is "
        "not known yet. Do not invent a file date. Do not pass date_from or date_to unless the user wrote them. "
        "Do not call tool_describe. "
        "periods is consolidated. filing_company is the filing company. Do not mix them and do not fill "
        "a missing line from the other series or with 0. "
        "Copy each line's unit and source.filing_page. "
        "If next_step is present, copy it. Do not write 取得しました or 確認できます. "
        "Do not write successfully retrieved, verified, or obtained using. "
        "That ban is the same as 取得しました."
    )}


def register(ctx):
    """Register every tool under the `edinet` toolset."""
    from . import client as _client
    from . import schemas as _schemas
    from . import settings as _settings
    from . import tools as _tools

    getter = getattr(ctx, "get_config", None)
    _settings.bind_config(getter if callable(getter) else None)

    for schema in _schemas.ALL_SCHEMAS:
        ctx.register_tool(
            name=schema["name"],
            toolset="edinet",
            schema=schema,
            handler=_tools.HANDLERS[schema["name"]],
            requires_env=[_client.API_KEY_ENV],
            emoji="📄",
        )
    try:
        ctx.register_hook("pre_llm_call", _nudge_edinet)
    except Exception:
        import logging
        logging.getLogger(__name__).warning("jp-edinet pre_llm_call hook unavailable", exc_info=True)
    try:
        from pathlib import Path
        ctx.register_skill(
            "edinet-filings",
            Path(__file__).resolve().parent / "skills" / "edinet-filings" / "SKILL.md",
            description=_SKILL_DESCRIPTION,
        )
    except Exception:
        import logging
        logging.getLogger(__name__).warning("jp-edinet skill was not registered", exc_info=True)
