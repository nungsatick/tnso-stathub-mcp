"""MCP resources exposing static reference information about TNSO StatHub."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from config import CONFIG


def register_resources(mcp: FastMCP) -> None:
    @mcp.resource("sdmx://info")
    def endpoint_info() -> str:
        """Basic information about the TNSO StatHub SDMX endpoint this server targets."""
        return (
            f"Agency: {CONFIG.agency_id} - {CONFIG.name}\n"
            f"Data/Structure API: {CONFIG.base_url}\n"
            f"Metadata Registry: {CONFIG.registry_base_url}\n"
            f"Public portal: {CONFIG.portal_url}\n"
            "Authentication: none required (public SDMX 2.1 REST endpoint).\n"
        )

    @mcp.resource("sdmx://formats/guide")
    def formats_guide() -> str:
        """Comparison of data formats supported by build_data_url()."""
        return (
            "csv - SDMX-CSV, one row per observation, easiest for spreadsheets/pandas.\n"
            "csvfilewithlabels - SDMX-CSV with human-readable labels alongside codes.\n"
            "json - SDMX-JSON, good for web apps.\n"
            "xml - SDMX-ML structure-specific data, preserves full metadata.\n"
        )

    @mcp.resource("sdmx://syntax/guide")
    def syntax_guide() -> str:
        """SDMX key syntax quick reference."""
        return (
            "Keys are dot-separated dimension values in dimension order, e.g. 'A..TH.'.\n"
            "Use '+' to request multiple values for one dimension: 'A+M..TH.'.\n"
            "Leave a dimension blank between dots to mean 'all values' for it.\n"
            "Use 'all' as the whole key to request every observation in a dataflow.\n"
        )
