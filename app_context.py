"""
Lifespan management for the TNSO StatHub MCP server.

Single-provider design: one SdmxClient (live TNSO calls) and one optional
local metadata cache (see cache_integration.py / stathub-cache/) are
created at server startup and shared by every tool call for the lifetime
of the process, then closed cleanly on shutdown.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from cache_integration import get_cache
from sdmx_client import SdmxClient

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


@dataclass
class AppContext:
    client: SdmxClient
    cache: Any | None = None  # TnsoCacheStore instance, or None if disabled


@asynccontextmanager
async def app_lifespan(server: "FastMCP") -> AsyncIterator[AppContext]:
    _ = server  # required by the lifespan protocol, unused here
    client = SdmxClient()
    cache = get_cache()
    try:
        yield AppContext(client=client, cache=cache)
    finally:
        await client.aclose()
        if cache is not None:
            cache.close()
