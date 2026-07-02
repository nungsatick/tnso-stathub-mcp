"""
Lifespan management for the TNSO StatHub MCP server.

Single-provider, single-client design: one SdmxClient is created at server
startup and shared by every tool call for the lifetime of the process, then
closed cleanly on shutdown.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sdmx_client import SdmxClient

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


@dataclass
class AppContext:
    client: SdmxClient


@asynccontextmanager
async def app_lifespan(server: "FastMCP") -> AsyncIterator[AppContext]:
    _ = server  # required by the lifespan protocol, unused here
    client = SdmxClient()
    try:
        yield AppContext(client=client)
    finally:
        await client.aclose()
