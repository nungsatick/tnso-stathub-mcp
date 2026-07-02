"""
TNSO StatHub MCP server entrypoint.

Usage:
    uv run python main_server.py                                   # STDIO (local clients)
    uv run python main_server.py --transport http --port 8000       # Streamable HTTP (remote)
    uv run mcp dev ./main_server.py                                 # MCP Inspector

Run `python main_server.py --help` for all options.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# This project uses flat, root-level modules (sdmx_client.py, config.py,
# utils.py) imported with absolute imports from tools/, resources/, and
# prompts/. That only works automatically when this file is run directly
# (`python main_server.py`), since Python then adds its own directory to
# sys.path. Some launchers - notably `mcp dev` / `uv run mcp dev` for the
# MCP Inspector - load this file via importlib instead, which does NOT add
# the project root to sys.path, causing "ModuleNotFoundError: No module
# named 'sdmx_client'" from tools/sdmx_tools.py. Insert it explicitly so
# every invocation method works the same way.
_PROJECT_ROOT = str(Path(__file__).resolve().parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from mcp.server.fastmcp import FastMCP  # noqa: E402

from app_context import app_lifespan  # noqa: E402
from prompts.sdmx_prompts import register_prompts  # noqa: E402
from resources.sdmx_resources import register_resources  # noqa: E402
from tools.sdmx_tools import register_tools  # noqa: E402


def build_server() -> FastMCP:
    mcp = FastMCP("TNSO StatHub Gateway", lifespan=app_lifespan)
    register_tools(mcp)
    register_resources(mcp)
    register_prompts(mcp)
    return mcp


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TNSO StatHub MCP server")
    parser.add_argument(
        "--transport",
        "-t",
        choices=["stdio", "http", "streamable-http"],
        default="stdio",
        help="Transport type (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("HOST", "0.0.0.0"),
        help="Host for HTTP transport (default: HOST env or 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        "-p",
        type=int,
        default=int(os.getenv("PORT", "8000")),
        help="Port for HTTP transport (default: PORT env or 8000)",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    # Keep logging off stdout in STDIO mode - it would corrupt the JSON-RPC stream.
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        stream=sys.stderr,
    )

    mcp = build_server()

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
