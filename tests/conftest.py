"""Load the repo root as a package (as Hermes does) and serve offline fixtures."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
PACKAGE = "edinet_plugin"
FAKE_API_KEY = "test-edinet-key-DO-NOT-USE-live-0000"

LIST_BY_DATE = {
    "2024-06-25": "list_2024-06-25.json",
    "2024-06-28": "list_2024-06-28.json",
    "2024-03-28": "list_2024-03-28.json",
}

CSV_BY_DOC = {
    "S100TR7I": "csv_toyota_ifrs_S100TR7I.zip",
    "S100TMMG": "csv_nintendo_jpgaap_S100TMMG.zip",
    "S100T58N": "csv_canon_usgaap_S100T58N.zip",
}


def _load_plugin():
    spec = importlib.util.spec_from_file_location(
        PACKAGE,
        PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = module
    spec.loader.exec_module(module)
    return module


def _minimal_zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("placeholder.txt", "ok")
    return buf.getvalue()


@pytest.fixture(scope="session")
def plugin():
    return sys.modules.get(PACKAGE) or _load_plugin()


@pytest.fixture(autouse=True)
def _isolate_hermes_home(tmp_path, monkeypatch):
    """Every test gets its own HERMES_HOME; nothing touches the real ~/.hermes."""
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    yield home


@pytest.fixture(autouse=True)
def _stub_plugin_data_dir(_isolate_hermes_home):
    """Cache uses plugin_data_dir. Tests stub it onto the isolated home."""
    import types

    home = _isolate_hermes_home

    def plugin_data_dir(name: str):
        if not isinstance(name, str) or not name or "/" in name or ".." in name:
            raise ValueError(f"invalid plugin name for storage: {name!r}")
        root = home / "plugin-data" / name
        root.mkdir(parents=True, exist_ok=True)
        return root

    storage = types.ModuleType("plugins.plugin_storage")
    storage.plugin_data_dir = plugin_data_dir
    previous_plugins = sys.modules.get("plugins")
    previous_storage = sys.modules.get("plugins.plugin_storage")
    plugins_mod = previous_plugins
    created_plugins = False
    if plugins_mod is None:
        plugins_mod = types.ModuleType("plugins")
        sys.modules["plugins"] = plugins_mod
        created_plugins = True
    sys.modules["plugins.plugin_storage"] = storage
    setattr(plugins_mod, "plugin_storage", storage)
    try:
        yield storage
    finally:
        if previous_storage is not None:
            sys.modules["plugins.plugin_storage"] = previous_storage
        else:
            sys.modules.pop("plugins.plugin_storage", None)
        if created_plugins:
            sys.modules.pop("plugins", None)
        elif previous_plugins is not None:
            if previous_storage is not None:
                setattr(previous_plugins, "plugin_storage", previous_storage)
            else:
                delattr(previous_plugins, "plugin_storage")


@pytest.fixture(autouse=True)
def _stub_hermes_write_guard():
    """Offline tests run outside Hermes; provide an allow-all write guard stub."""
    import types

    previous_agent = sys.modules.get("agent")
    previous_fs = sys.modules.get("agent.file_safety")
    fake = types.ModuleType("agent")
    fake_fs = types.ModuleType("agent.file_safety")
    fake_fs.get_write_denied_error = lambda path, **kwargs: None
    sys.modules["agent"] = fake
    sys.modules["agent.file_safety"] = fake_fs
    try:
        yield
    finally:
        if previous_fs is not None:
            sys.modules["agent.file_safety"] = previous_fs
        else:
            sys.modules.pop("agent.file_safety", None)
        if previous_agent is not None:
            sys.modules["agent"] = previous_agent
        else:
            sys.modules.pop("agent", None)


@pytest.fixture
def api(plugin, monkeypatch):
    """Monkeypatch network: list JSON and document bytes come from fixtures only."""
    calls: list[tuple] = []

    def fake_list(date: str):
        calls.append(("list", date))
        name = LIST_BY_DATE.get(date)
        if not name:
            return {
                "metadata": {
                    "title": "empty",
                    "parameter": {"date": date, "type": "2"},
                    "resultset": {"count": 0},
                    "status": "200",
                    "message": "OK",
                },
                "results": [],
            }
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    def fake_bytes(doc_id: str, doc_type: int):
        calls.append(("doc", doc_id, doc_type))
        if doc_type == 5 and doc_id in CSV_BY_DOC:
            return (FIXTURES / CSV_BY_DOC[doc_id]).read_bytes()
        if doc_type == 2:
            return (FIXTURES / "sample.pdf").read_bytes()
        if doc_type == 1:
            return _minimal_zip_bytes()
        raise plugin.errors.EdinetError(
            f"No fixture for doc_id={doc_id} type={doc_type}",
            kind="not_found",
            next_step="Use a fixture doc_id in tests.",
        )

    monkeypatch.setenv("EDINET_API_KEY", FAKE_API_KEY)
    monkeypatch.setattr(plugin.client, "fetch_documents_list", fake_list)
    monkeypatch.setattr(plugin.client, "fetch_document_bytes", fake_bytes)
    monkeypatch.setattr(plugin.client, "RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr(plugin.client, "MIN_REQUEST_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(
        plugin.client,
        "_http_get_bytes",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("live _http_get_bytes must not be called in tests")
        ),
    )
    return calls


@pytest.fixture
def http_bytes(plugin, monkeypatch):
    """Patch ``_http_get_bytes`` so classify/fetch paths run (HTTP-200 JSON errors)."""
    state: dict = {"handler": None, "calls": []}

    def _http_get_bytes(path: str, params: dict, api_key: str, *, resource: str = "document") -> bytes:
        state["calls"].append((path, dict(params), resource))
        handler = state["handler"]
        if handler is None:
            raise AssertionError("http_bytes handler not set")
        raw = handler(path, params, api_key)
        # Mirror production: classify after a successful HTTP read.
        plugin.client._classify_edinet_body(raw, api_key=api_key, resource=resource)
        return raw

    monkeypatch.setenv("EDINET_API_KEY", FAKE_API_KEY)
    monkeypatch.setattr(plugin.client, "RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr(plugin.client, "_http_get_bytes", _http_get_bytes)
    return state


@pytest.fixture
def run(plugin, api):
    def _run(tool: str, args: dict):
        raw = plugin.tools.HANDLERS[tool](args)
        assert isinstance(raw, str)
        assert FAKE_API_KEY not in raw
        assert "Subscription-Key" not in raw
        return json.loads(raw)

    return _run
