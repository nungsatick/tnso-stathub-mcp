"""
Shared utilities and constants for the TNSO StatHub MCP server.

Validation helpers follow the SDMX 2.1 specification's syntax rules for
dataflow identifiers, keys, providers, and reporting periods.
"""

from __future__ import annotations

import re

# SDMX 2.1 XML namespaces used throughout structure/common messages.
SDMX_NAMESPACES = {
    "mes": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message",
    "str": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure",
    "com": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common",
}

DATAFLOW_ID_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_\-]*$")
_VALUE_PATTERN = r"[A-Za-z0-9_@$\-]+"
_DIMENSION_PATTERN = f"({_VALUE_PATTERN}(\\+{_VALUE_PATTERN})*)?"
KEY_PATTERN = re.compile(f"^{_DIMENSION_PATTERN}(\\.{_DIMENSION_PATTERN})*$")

_PERIOD_PATTERNS = [
    re.compile(r"^\d{4}$"),  # 2023
    re.compile(r"^\d{4}-(0[1-9]|1[0-2])$"),  # 2023-01
    re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"),  # 2023-01-15
    re.compile(
        r"^\d{4}-(A1|S[12]|Q[1-4]|M(0[1-9]|1[0-2])|W(0[1-9]|[1-4]\d|5[0-3]))$"
    ),  # SDMX reporting periods
]


def validate_dataflow_id(dataflow_id: str) -> bool:
    """Return True if `dataflow_id` follows SDMX identifier syntax."""
    return bool(dataflow_id) and bool(DATAFLOW_ID_PATTERN.match(dataflow_id))


def validate_sdmx_key(key: str) -> bool:
    """Return True if `key` is a syntactically valid SDMX 2.1 data key.

    Valid examples: "all", "A.TH.POP", "A+M.TH+BKK.POP", "..TH..", "A..."
    """
    if key == "all":
        return True
    return bool(KEY_PATTERN.match(key))


def validate_period(period: str) -> bool:
    """Return True if `period` is a valid ISO-8601 or SDMX reporting period."""
    if not period:
        return False
    return any(p.match(period) for p in _PERIOD_PATTERNS)


def filter_dataflows_by_keywords(dataflows: list[dict], keywords: list[str]) -> list[dict]:
    """Score and sort dataflows by how many keywords match name/description/id."""
    if not keywords:
        return dataflows

    scored = []
    for df in dataflows:
        haystack = f"{df.get('name', '')} {df.get('description', '')} {df.get('id', '')}".lower()
        score = sum(1 for kw in keywords if kw.lower() in haystack)
        if score > 0:
            scored.append({**df, "relevance_score": score})

    return sorted(scored, key=lambda d: d["relevance_score"], reverse=True)


def qname(prefix: str, tag: str) -> str:
    """Build an ElementTree-compatible qualified tag, e.g. qname('str', 'Dataflow')."""
    return f"{{{SDMX_NAMESPACES[prefix]}}}{tag}"
