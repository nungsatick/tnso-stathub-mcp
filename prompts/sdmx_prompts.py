"""Guided MCP prompts for exploring TNSO StatHub data."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def register_prompts(mcp: FastMCP) -> None:
    @mcp.prompt()
    def discovery_guide(topic: str = "") -> str:
        """Step-by-step guide for finding and retrieving TNSO statistics on a topic."""
        topic_line = f' about "{topic}"' if topic else ""
        return (
            f"Help me find TNSO (Thai National Statistical Office) data{topic_line}. "
            "Follow this workflow:\n"
            "1. Call list_dataflows(keywords=[...]) to find relevant dataflows.\n"
            "2. Call get_dataflow_structure(dataflow_id) on the best match to see its dimensions.\n"
            "3. Call get_dimension_codes(dataflow_id, dimension_id, search_term=...) to find the "
            "right code for each dimension you care about (e.g. a province or year).\n"
            "4. Call get_data_availability(dataflow_id, key) to confirm data exists before "
            "fetching it.\n"
            "5. Call build_data_url(dataflow_id, key, format_type) to get a direct data URL, "
            "then fetch it.\n"
        )

    @mcp.prompt()
    def troubleshooting_guide() -> str:
        """Common issues when querying TNSO StatHub and how to resolve them."""
        return (
            "If a tool call fails:\n"
            "- 404 on get_dataflow_structure: double-check the dataflow id from "
            "list_dataflows(); ids are case-sensitive.\n"
            "- Empty results from get_dimension_codes: the dimension may use a codelist owned "
            "by another agency - check codelist_agency in get_dataflow_structure()'s output.\n"
            "- get_data_availability reports has_data=False: try a broader key (fewer fixed "
            "dimensions, or key='all') to confirm the dataflow has any data at all.\n"
            "- Network/timeout errors: TNSO's API is public but rate limits are undocumented; "
            "retry with backoff.\n"
        )
