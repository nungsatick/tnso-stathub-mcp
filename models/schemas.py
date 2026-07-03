"""
Pydantic output schemas for TNSO StatHub MCP tools.

Every tool returns one of these models so MCP clients receive validated,
typed, structured data (via FastMCP's structuredContent support) rather than
free-form text.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class DataflowSummary(BaseModel):
    id: str
    agency: str
    version: str
    name: str
    name_th: str = ""
    description: str = ""


class Pagination(BaseModel):
    has_more: bool
    next_offset: int | None = None
    total_pages: int
    current_page: int


class DataflowListResult(BaseModel):
    agency_id: str
    total_found: int
    showing: int
    offset: int
    limit: int
    dataflows: list[DataflowSummary]
    pagination: Pagination
    next_step: str = "Use get_dataflow_structure() to explore a dataflow's dimensions."


class DimensionInfo(BaseModel):
    id: str
    position: int
    type: str  # "Dimension" | "TimeDimension"
    codelist_id: str | None = None
    codelist_agency: str | None = None


class DataflowStructureResult(BaseModel):
    dataflow_id: str
    agency_id: str
    version: str
    name: str
    name_th: str = ""
    dsd_id: str | None = None
    dimensions: list[DimensionInfo]
    next_step: str = (
        "Use get_dimension_codes() to look up codes for a dimension, "
        "then build_data_url() to construct a query."
    )


class CodeItem(BaseModel):
    id: str
    name: str
    name_th: str = ""


class DimensionCodesResult(BaseModel):
    dataflow_id: str
    dimension_id: str
    codelist_id: str | None = None
    total_codes: int
    showing: int
    search_term: str | None = None
    codes: list[CodeItem]


class DataAvailabilityResult(BaseModel):
    dataflow_id: str
    key: str
    method: str  # "availableconstraint" | "serieskeysonly_probe"
    has_data: bool
    series_count: int | None = None
    time_period_start: str | None = None
    time_period_end: str | None = None
    notes: str = ""


class ValidationResult(BaseModel):
    dataflow_id: str
    key: str
    dataflow_id_valid: bool
    key_syntax_valid: bool
    issues: list[str] = Field(default_factory=list)
    is_valid: bool


class DataUrlResult(BaseModel):
    dataflow_id: str
    key: str
    format_type: str
    url: str
    notes: str = ""


class CodelistResult(BaseModel):
    codelist_id: str
    agency_id: str
    version: str
    name: str
    name_th: str = ""
    total_codes: int
    codes: list[CodeItem]


class EndpointInfo(BaseModel):
    agency_id: str
    name: str
    base_url: str
    registry_base_url: str
    portal_url: str


class DataFetchResult(BaseModel):
    dataflow_id: str
    key: str
    total_rows: int
    returned_rows: int
    truncated: bool
    file_path: str | None = None
    preview_csv: str
    source_url: str
