"""
Tests for the cache-aware fetch helpers in tools/sdmx_tools.py
(_get_all_dataflows_cached, _get_structure_cached, _get_codelist_cached).

These call the helpers directly with a fake AppContext (a fake client that
counts calls + a real TnsoCacheStore against a temp SQLite file), bypassing
FastMCP's Context injection entirely - unit-testing the tool bodies
end-to-end through a live MCP Context proved impractical (see README /
project history), but the caching *logic* itself is plain async functions
that take an app-context-shaped object, so it's directly testable.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# stathub-cache is a sibling of tnso-stathub-mcp in this dev environment;
# skip this whole module if it isn't present (mirrors cache_integration.py's
# own optional-dependency behavior).
_CACHE_DIR = _ROOT.parent / "stathub-cache"
if not _CACHE_DIR.is_dir():
    pytest.skip("stathub-cache sibling folder not present", allow_module_level=True)
sys.path.insert(0, str(_CACHE_DIR))

from cache_store import TnsoCacheStore  # noqa: E402
from sdmx_client import DimensionInfo  # noqa: E402
from tools.sdmx_tools import (  # noqa: E402
    _get_all_dataflows_cached,
    _get_codelist_cached,
    _get_structure_cached,
)


class FakeClient:
    """Counts calls so tests can assert whether the cache was actually used."""

    def __init__(self):
        self.list_dataflows_calls = 0
        self.get_dataflow_structure_calls = 0
        self.get_codelist_calls = 0

    async def list_dataflows(self):
        self.list_dataflows_calls += 1
        return [{"id": "DF_A", "agency": "TNSO", "version": "1.0", "name": "Alpha", "description": ""}]

    async def get_dataflow_structure(self, dataflow_id, version="latest"):
        self.get_dataflow_structure_calls += 1
        return {
            "dataflow_id": dataflow_id,
            "agency_id": "TNSO",
            "version": "1.1",  # resolved version differs from the "latest" alias on purpose
            "name": "Public Garden Statistics",
            "dsd_id": "DSD_X",
            "dimensions": [
                DimensionInfo(id="GEO", position=0, type="Dimension", codelist_id="CL_GEO", codelist_agency="TNSO"),
            ],
        }

    async def get_codelist(self, codelist_id, agency=None, version="latest"):
        self.get_codelist_calls += 1
        return {
            "codelist_id": codelist_id,
            "agency_id": agency or "TNSO",
            "version": "1.0",
            "name": "Geography",
            "codes": [{"id": "BKK", "name": "Bangkok"}],
        }


@pytest.fixture
def cache(tmp_path):
    store = TnsoCacheStore(tmp_path / "test_cache.db")
    yield store
    store.close()


def make_app(cache_store):
    return SimpleNamespace(client=FakeClient(), cache=cache_store)


@pytest.mark.asyncio
async def test_get_all_dataflows_cached_hits_client_once(cache):
    app = make_app(cache)
    first = await _get_all_dataflows_cached(app)
    second = await _get_all_dataflows_cached(app)
    assert first == second
    assert app.client.list_dataflows_calls == 1  # second call served from cache


@pytest.mark.asyncio
async def test_get_all_dataflows_without_cache_hits_client_every_time():
    app = make_app(None)
    await _get_all_dataflows_cached(app)
    await _get_all_dataflows_cached(app)
    assert app.client.list_dataflows_calls == 2


@pytest.mark.asyncio
async def test_get_structure_cached_hits_client_once_per_version_alias(cache):
    app = make_app(cache)
    first = await _get_structure_cached(app, "DF_X", "latest")
    second = await _get_structure_cached(app, "DF_X", "latest")
    assert app.client.get_dataflow_structure_calls == 1
    assert first["dsd_id"] == second["dsd_id"] == "DSD_X"
    # dimensions come back as dicts from the cache path, dataclasses from
    # the live path - the tool layer's _dget() helper handles both.
    assert second["dimensions"][0]["id"] == "GEO"


@pytest.mark.asyncio
async def test_get_structure_cached_different_version_alias_is_separate_entry(cache):
    app = make_app(cache)
    await _get_structure_cached(app, "DF_X", "latest")
    await _get_structure_cached(app, "DF_X", "1.1")  # different requested alias -> cache miss
    assert app.client.get_dataflow_structure_calls == 2


@pytest.mark.asyncio
async def test_get_codelist_cached_hits_client_once(cache):
    app = make_app(cache)
    first = await _get_codelist_cached(app, "CL_GEO", "TNSO", "latest")
    second = await _get_codelist_cached(app, "CL_GEO", "TNSO", "latest")
    assert app.client.get_codelist_calls == 1
    assert first["codes"] == second["codes"] == [{"id": "BKK", "name": "Bangkok"}]
