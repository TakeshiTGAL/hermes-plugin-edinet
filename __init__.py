"""jp-edinet: EDINET filings and headline financials for Hermes Agent.

Three tools: search filings by company and file date, extract key metrics from
the CSV ZIP, and save PDF/ZIP documents into Hermes' cache.
"""

# Hermes imports this directory as a package. pytest's collector also imports this
# file on its own (the repo root is the package), where relative imports cannot work.
if __package__:
    from . import cache, citation, client, metrics, save, schemas, settings, tools  # noqa: F401


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
