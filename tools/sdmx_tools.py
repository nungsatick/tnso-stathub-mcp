"""
MCP tool definitions for progressive SDMX discovery against TNSO StatHub.

Each tool is intentionally narrow in scope (one step of the discovery
workflow) so an LLM agent can compose them instead of receiving one huge
metadata dump. See README.md "Usage Examples" for the intended call order:

    list_dataflows -> get_dataflow_structure -> get_dimension_codes
    -> get_data_availability -> build_data_url / fetch_data
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import Context, FastMCP

from config import CONFIG
from models.schemas import (
    CodelistResult,
    CodeItem,
    DataAvailabilityResult,
    DataFetchResult,
    DataflowListResult,
    DataflowStructureResult,
    DataflowSummary,
    DataUrlResult,
    DimensionCodesResult,
    DimensionInfo as DimensionInfoModel,
    Pagination,
    ValidationResult,
)
from sdmx_client import SdmxClient, SdmxClientError
from utils import filter_dataflows_by_keywords, validate_dataflow_id, validate_sdmx_key

# Downloaded CSVs are saved here (relative to the project root) so they can
# be opened directly in Excel/pandas. Only meaningful when this server runs
# locally (uv run) - if deployed remotely over HTTP, this is server-side
# storage the caller may not have filesystem access to.
DOWNLOADS_DIR = Path(__file__).resolve().parent.parent / "downloads"


def _app_ctx(ctx: Context):
    return ctx.request_context.lifespan_context


def register_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    async def list_dataflows(
        ctx: Context,
        keywords: list[str] | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> DataflowListResult:
        """
        Find TNSO dataflows by keyword. Start here for any new data request.

        Args:
            keywords: Words to match against dataflow name/description/id
                (e.g. ["population", "province"]). Omit to list everything.
            limit: Max dataflows to return (default 10).
            offset: Pagination offset (default 0).
        """
        client = _app_ctx(ctx).client
        try:
            all_flows = await client.list_dataflows()
        except SdmxClientError as exc:
            raise ValueError(str(exc)) from exc

        matched = filter_dataflows_by_keywords(all_flows, keywords or [])
        pool = matched if keywords else all_flows
        total = len(pool)
        page = pool[offset : offset + limit]

        return DataflowListResult(
            agency_id=CONFIG.agency_id,
            total_found=total,
            showing=len(page),
            offset=offset,
            limit=limit,
            dataflows=[DataflowSummary(**d) for d in page],
            pagination=Pagination(
                has_more=offset + limit < total,
                next_offset=offset + limit if offset + limit < total else None,
                total_pages=max(1, (total + limit - 1) // limit),
                current_page=offset // limit + 1,
            ),
        )

    @mcp.tool()
    async def get_dataflow_structure(
        ctx: Context, dataflow_id: str, version: str = "latest"
    ) -> DataflowStructureResult:
        """
        Get a dataflow's dimensions (and which codelist backs each one).

        Args:
            dataflow_id: Dataflow id from list_dataflows(), e.g. "DF_03GARD_PUB".
            version: Dataflow version, or "latest" (default).
        """
        client = _app_ctx(ctx).client
        try:
            structure = await client.get_dataflow_structure(dataflow_id, version)
        except SdmxClientError as exc:
            raise ValueError(str(exc)) from exc

        return DataflowStructureResult(
            dataflow_id=structure["dataflow_id"],
            agency_id=structure["agency_id"],
            version=structure["version"],
            name=structure["name"],
            dsd_id=structure["dsd_id"],
            dimensions=[
                DimensionInfoModel(
                    id=d.id,
                    position=d.position,
                    type=d.type,
                    codelist_id=d.codelist_id,
                    codelist_agency=d.codelist_agency,
                )
                for d in structure["dimensions"]
            ],
        )

    @mcp.tool()
    async def get_dimension_codes(
        ctx: Context,
        dataflow_id: str,
        dimension_id: str,
        search_term: str | None = None,
        limit: int = 50,
        version: str = "latest",
    ) -> DimensionCodesResult:
        """
        Browse (or search) the codes available for one dimension of a dataflow.

        Args:
            dataflow_id: Dataflow id, e.g. "DF_03GARD_PUB".
            dimension_id: Dimension id from get_dataflow_structure(), e.g. "GEO".
            search_term: Optional case-insensitive substring to filter codes
                by id or name (e.g. "bangkok").
            limit: Max codes to return (default 50).
            version: Dataflow version, or "latest" (default).
        """
        client = _app_ctx(ctx).client
        try:
            structure = await client.get_dataflow_structure(dataflow_id, version)
        except SdmxClientError as exc:
            raise ValueError(str(exc)) from exc

        dim = next((d for d in structure["dimensions"] if d.id == dimension_id), None)
        if dim is None:
            known = ", ".join(d.id for d in structure["dimensions"])
            raise ValueError(f"Dimension '{dimension_id}' not found. Known dimensions: {known}")
        if dim.codelist_id is None:
            raise ValueError(f"Dimension '{dimension_id}' has no enumerated codelist (likely free text).")

        try:
            codelist = await client.get_codelist(dim.codelist_id, dim.codelist_agency)
        except SdmxClientError as exc:
            raise ValueError(str(exc)) from exc

        codes = codelist["codes"]
        if search_term:
            term = search_term.lower()
            codes = [c for c in codes if term in c["id"].lower() or term in c["name"].lower()]

        return DimensionCodesResult(
            dataflow_id=dataflow_id,
            dimension_id=dimension_id,
            codelist_id=dim.codelist_id,
            total_codes=len(codes),
            showing=min(limit, len(codes)),
            search_term=search_term,
            codes=[CodeItem(**c) for c in codes[:limit]],
        )

    @mcp.tool()
    async def get_data_availability(
        ctx: Context, dataflow_id: str, key: str = "all", version: str = "latest"
    ) -> DataAvailabilityResult:
        """
        Check whether data actually exists for a dataflow/key before querying it.

        Args:
            dataflow_id: Dataflow id, e.g. "DF_03GARD_PUB".
            key: SDMX key built from build_key-style dot-separated dimension
                values (e.g. "A..TH.") or "all" to check the whole dataflow.
            version: Dataflow version, or "latest" (default).
        """
        client = _app_ctx(ctx).client
        try:
            result = await client.probe_data_availability(dataflow_id, key, version)
        except SdmxClientError as exc:
            raise ValueError(str(exc)) from exc

        return DataAvailabilityResult(dataflow_id=dataflow_id, key=key, **result)

    @mcp.tool()
    def validate_query(dataflow_id: str, key: str) -> ValidationResult:
        """
        Validate a dataflow id and SDMX key's syntax before querying (no network call).

        Args:
            dataflow_id: Dataflow id to check.
            key: SDMX key to check, e.g. "A..TH." or "all".
        """
        issues = []
        id_ok = validate_dataflow_id(dataflow_id)
        key_ok = validate_sdmx_key(key)
        if not id_ok:
            issues.append(f"'{dataflow_id}' is not a valid SDMX identifier (must start with a letter).")
        if not key_ok:
            issues.append(f"'{key}' is not valid SDMX key syntax (dot-separated, '+' joins multiple values).")

        return ValidationResult(
            dataflow_id=dataflow_id,
            key=key,
            dataflow_id_valid=id_ok,
            key_syntax_valid=key_ok,
            issues=issues,
            is_valid=id_ok and key_ok,
        )

    @mcp.tool()
    def build_data_url(
        dataflow_id: str,
        key: str = "all",
        format_type: str = "csv",
        start_period: str | None = None,
        end_period: str | None = None,
        version: str = "latest",
    ) -> DataUrlResult:
        """
        Build a ready-to-fetch data URL for a dataflow/key (no network call).

        Args:
            dataflow_id: Dataflow id, e.g. "DF_03GARD_PUB".
            key: SDMX key (dot-separated dimension values, or "all").
            format_type: "csv", "csvfilewithlabels", "json", or "xml".
            start_period: Optional start period, e.g. "2020" or "2020-Q1".
            end_period: Optional end period.
            version: Dataflow version, or "latest" (default).
        """
        url = SdmxClient.build_data_url(
            dataflow_id=dataflow_id,
            key=key,
            format_type=format_type,
            start_period=start_period,
            end_period=end_period,
            version=version,
        )
        return DataUrlResult(
            dataflow_id=dataflow_id,
            key=key,
            format_type=format_type,
            url=url,
            notes="GET this URL directly (no auth required) to retrieve the data.",
        )

    @mcp.tool()
    async def fetch_data(
        ctx: Context,
        dataflow_id: str,
        key: str = "all",
        version: str = "latest",
        start_period: str | None = None,
        end_period: str | None = None,
        max_preview_rows: int = 200,
        save_to_file: bool = True,
    ) -> DataFetchResult:
        """
        Download the actual observation data for a dataflow (not just metadata).

        Fetches real data from TNSO as CSV. Since this server runs locally, the
        full CSV is also saved to disk (downloads/, next to the project) by
        default so it can be opened directly in Excel or loaded with pandas -
        set save_to_file=False to skip. Only up to max_preview_rows data rows
        are returned inline in the tool result, to keep responses small; check
        `truncated` / `total_rows` to see whether the preview is everything.

        Args:
            dataflow_id: Dataflow id, e.g. "DF_01DI_IND_AGING".
            key: SDMX key (dot-separated dimension values, or "all"). Use
                get_data_availability() first to confirm the key has data.
            version: Dataflow version, or "latest" (default).
            start_period: Optional start period, e.g. "2020".
            end_period: Optional end period.
            max_preview_rows: Max data rows to include inline (default 200).
            save_to_file: Save the full CSV under downloads/ (default True).
        """
        client = _app_ctx(ctx).client
        try:
            csv_text = await client.fetch_data_csv(
                dataflow_id, key, version, start_period, end_period
            )
        except SdmxClientError as exc:
            raise ValueError(str(exc)) from exc

        rows = list(csv.reader(io.StringIO(csv_text)))
        header, data_rows = (rows[0], rows[1:]) if rows else ([], [])
        total_rows = len(data_rows)
        preview_rows = data_rows[:max_preview_rows]
        truncated = total_rows > len(preview_rows)

        preview_buf = io.StringIO()
        writer = csv.writer(preview_buf)
        if header:
            writer.writerow(header)
        writer.writerows(preview_rows)

        file_path = None
        if save_to_file:
            DOWNLOADS_DIR.mkdir(exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            safe_key = "all" if key == "all" else key.replace("+", "_").replace(".", "-")
            out_path = DOWNLOADS_DIR / f"{dataflow_id}_{safe_key}_{timestamp}.csv"
            out_path.write_text(csv_text, encoding="utf-8")
            file_path = str(out_path.resolve())

        source_url = SdmxClient.build_data_url(
            dataflow_id, key, "csv", start_period, end_period, version
        )

        return DataFetchResult(
            dataflow_id=dataflow_id,
            key=key,
            total_rows=total_rows,
            returned_rows=len(preview_rows),
            truncated=truncated,
            file_path=file_path,
            preview_csv=preview_buf.getvalue(),
            source_url=source_url,
        )

    @mcp.tool()
    async def get_codelist(
        ctx: Context, codelist_id: str, agency: str | None = None, version: str = "latest"
    ) -> CodelistResult:
        """
        Browse a codelist directly by id (when you already know it).

        Args:
            codelist_id: Codelist id, e.g. "CL_GEO".
            agency: Owning agency, defaults to TNSO.
            version: Codelist version, or "latest" (default).
        """
        client = _app_ctx(ctx).client
        try:
            codelist = await client.get_codelist(codelist_id, agency, version)
        except SdmxClientError as exc:
            raise ValueError(str(exc)) from exc

        return CodelistResult(
            codelist_id=codelist["codelist_id"],
            agency_id=codelist["agency_id"],
            version=codelist["version"],
            name=codelist["name"],
            total_codes=len(codelist["codes"]),
            codes=[CodeItem(**c) for c in codelist["codes"]],
        )
