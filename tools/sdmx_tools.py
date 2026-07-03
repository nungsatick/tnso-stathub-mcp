"""
MCP tool definitions for progressive SDMX discovery against TNSO StatHub.

Each tool is intentionally narrow in scope (one step of the discovery
workflow) so an LLM agent can compose them instead of receiving one huge
metadata dump. See README.md "Usage Examples" for the intended call order:

    list_dataflows -> get_dataflow_structure -> get_dimension_codes
    -> get_data_availability -> build_data_url / fetch_data

fetch_data_labeled() is a shortcut that combines fetch_data() with a
get_dimension_codes()-style lookup for every coded dimension, returning
CSV with EN/TH label columns already joined in.

Metadata-fetching tools (list_dataflows, get_dataflow_structure,
get_dimension_codes, get_codelist) go through an optional local cache
(see cache_integration.py / ../stathub-cache) so repeat lookups skip the
network + XML parsing round trip. If that cache isn't present, every tool
falls back to a live TNSO call exactly as before - see the *_cached()
helpers below.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from app_context import AppContext
from cache_integration import CODELIST_TTL, DATAFLOWS_TTL, STRUCTURE_TTL
from config import CONFIG
from models.schemas import (
    CodelistResult,
    CodeItem,
    DataAvailabilityResult,
    DataDictionaryResult,
    DataFetchLabeledResult,
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


def _app_ctx(ctx: Context) -> AppContext:
    return ctx.request_context.lifespan_context


def _dget(obj: Any, key: str) -> Any:
    """Read a field from either a sdmx_client.DimensionInfo dataclass
    instance (live fetch) or a plain dict (cache-store round trip) -
    cached dimensions come back as dicts, live ones as dataclasses."""
    return obj[key] if isinstance(obj, dict) else getattr(obj, key)


# ----------------------------------------------------------------------
# Cache-aware fetch helpers, shared by multiple tools below.
# ----------------------------------------------------------------------


async def _get_all_dataflows_cached(app: AppContext) -> list[dict[str, str]]:
    cache = app.cache
    if cache is not None and cache.is_fresh("dataflows:all", DATAFLOWS_TTL):
        cached = cache.get_dataflows()
        if cached:
            return cached
    flows = await app.client.list_dataflows()
    if cache is not None:
        cache.save_dataflows(flows)
    return flows


async def _get_structure_cached(app: AppContext, dataflow_id: str, version: str) -> dict[str, Any]:
    cache = app.cache
    cache_key = f"structure:{dataflow_id}:{version}"
    if cache is not None and cache.is_fresh(cache_key, STRUCTURE_TTL):
        cached = cache.get_dataflow_structure(dataflow_id, version)
        if cached is not None:
            return cached
    structure = await app.client.get_dataflow_structure(dataflow_id, version)
    if cache is not None:
        cache.save_dataflow_structure(structure, cache_version=version)
    return structure


async def _get_codelist_cached(
    app: AppContext, codelist_id: str, agency: str | None, version: str
) -> dict[str, Any]:
    cache = app.cache
    resolved_agency = agency or CONFIG.agency_id
    cache_key = f"codelist:{codelist_id}:{resolved_agency}:{version}"
    if cache is not None and cache.is_fresh(cache_key, CODELIST_TTL):
        cached = cache.get_codelist(codelist_id, resolved_agency, version)
        if cached is not None:
            return cached
    codelist = await app.client.get_codelist(codelist_id, agency, version)
    if cache is not None:
        cache.save_codelist(codelist, cache_version=version)
    return codelist


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
        app = _app_ctx(ctx)
        try:
            all_flows = await _get_all_dataflows_cached(app)
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
        app = _app_ctx(ctx)
        try:
            structure = await _get_structure_cached(app, dataflow_id, version)
        except SdmxClientError as exc:
            raise ValueError(str(exc)) from exc

        return DataflowStructureResult(
            dataflow_id=structure["dataflow_id"],
            agency_id=structure["agency_id"],
            version=structure["version"],
            name=structure["name"],
            name_th=structure.get("name_th", ""),
            dsd_id=structure["dsd_id"],
            dimensions=[
                DimensionInfoModel(
                    id=_dget(d, "id"),
                    position=_dget(d, "position"),
                    type=_dget(d, "type"),
                    codelist_id=_dget(d, "codelist_id"),
                    codelist_agency=_dget(d, "codelist_agency"),
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
        app = _app_ctx(ctx)
        try:
            structure = await _get_structure_cached(app, dataflow_id, version)
        except SdmxClientError as exc:
            raise ValueError(str(exc)) from exc

        dims = structure["dimensions"]
        dim = next((d for d in dims if _dget(d, "id") == dimension_id), None)
        if dim is None:
            known = ", ".join(_dget(d, "id") for d in dims)
            raise ValueError(f"Dimension '{dimension_id}' not found. Known dimensions: {known}")
        codelist_id = _dget(dim, "codelist_id")
        codelist_agency = _dget(dim, "codelist_agency")
        if codelist_id is None:
            raise ValueError(f"Dimension '{dimension_id}' has no enumerated codelist (likely free text).")

        try:
            codelist = await _get_codelist_cached(app, codelist_id, codelist_agency, "latest")
        except SdmxClientError as exc:
            raise ValueError(str(exc)) from exc

        codes = codelist["codes"]
        if search_term:
            term = search_term.lower()
            codes = [c for c in codes if term in c["id"].lower() or term in c["name"].lower()]

        return DimensionCodesResult(
            dataflow_id=dataflow_id,
            dimension_id=dimension_id,
            codelist_id=codelist_id,
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

        Not cached (Phase 1 caches metadata only) - data availability can
        change more often than dataflow/codelist metadata.

        Args:
            dataflow_id: Dataflow id, e.g. "DF_03GARD_PUB".
            key: SDMX key built from dot-separated dimension values (e.g.
                "A..TH.") or "all" to check the whole dataflow.
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
    async def build_data_url(
        ctx: Context,
        dataflow_id: str,
        key: str = "all",
        format_type: str = "csv",
        start_period: str | None = None,
        end_period: str | None = None,
        version: str = "latest",
    ) -> DataUrlResult:
        """
        Build a ready-to-fetch data URL for a dataflow/key.

        Args:
            dataflow_id: Dataflow id, e.g. "DF_03GARD_PUB".
            key: SDMX key (dot-separated dimension values, or "all").
            format_type: "csv", "csvfilewithlabels", "json", or "xml".
            start_period: Optional start period, e.g. "2020" or "2020-Q1".
            end_period: Optional end period.
            version: Dataflow version, or "latest" (default). TNSO's /data
                endpoint rejects the literal "latest" (unlike /dataflow), so
                "latest" is resolved to the actual version string (e.g.
                "1.0") via the cached/live dataflow structure first.
        """
        app = _app_ctx(ctx)
        resolved_version = version
        if version == "latest":
            try:
                structure = await _get_structure_cached(app, dataflow_id, "latest")
            except SdmxClientError as exc:
                raise ValueError(str(exc)) from exc
            resolved_version = structure["version"] or version

        url = SdmxClient.build_data_url(
            dataflow_id=dataflow_id,
            key=key,
            format_type=format_type,
            start_period=start_period,
            end_period=end_period,
            version=resolved_version,
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
        app = _app_ctx(ctx)
        client = app.client
        resolved_version = version
        if version == "latest":
            try:
                structure = await _get_structure_cached(app, dataflow_id, "latest")
            except SdmxClientError as exc:
                raise ValueError(str(exc)) from exc
            resolved_version = structure["version"] or version

        try:
            csv_text = await client.fetch_data_csv(
                dataflow_id, key, resolved_version, start_period, end_period
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
            # utf-8-sig (UTF-8 with BOM) so Excel on Windows detects the
            # encoding automatically and renders Thai text correctly
            # instead of mojibake - plain "utf-8" looks fine in a text
            # editor but Excel defaults to the system codepage without a
            # BOM. newline="" is required here: csv_text already has \r\n
            # row terminators (TNSO's CSV), and Path.write_text()/open() in
            # default text mode additionally translates every embedded \n
            # to os.linesep on Windows, turning \r\n into \r\r\n and
            # showing as a blank line after every row in Excel.
            with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
                f.write(csv_text)
            file_path = str(out_path.resolve())

        source_url = SdmxClient.build_data_url(
            dataflow_id, key, "csv", start_period, end_period, resolved_version
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
    async def fetch_data_labeled(
        ctx: Context,
        dataflow_id: str,
        key: str = "all",
        version: str = "latest",
        start_period: str | None = None,
        end_period: str | None = None,
        label_mode: str = "both",
        max_preview_rows: int = 200,
        save_to_file: bool = True,
    ) -> DataFetchLabeledResult:
        """
        Download dataflow data with human-readable labels joined in for
        every coded dimension, in one call - no separate
        get_dimension_codes() + manual merge needed.

        Labels come from the cached codelists (same source as
        get_dimension_codes()), so repeat calls are fast. Otherwise behaves
        like fetch_data(): saves the full labeled CSV under downloads/ by
        default and returns up to max_preview_rows inline.

        Args:
            dataflow_id: Dataflow id, e.g. "DF_01DI_IND_AGING".
            key: SDMX key (dot-separated dimension values, or "all"). Use
                get_data_availability() first to confirm the key has data.
            version: Dataflow version, or "latest" (default).
            start_period: Optional start period, e.g. "2020".
            end_period: Optional end period.
            label_mode: How to render each coded dimension column -
                "both" (default): keep the raw code and insert
                "<DIM>_LABEL_EN"/"<DIM>_LABEL_TH" columns right after it;
                "th": replace the raw code in place with its Thai label
                (falls back to the raw code if TNSO has no Thai label for
                it - the column is never left blank); "en": same but with
                the English label. "th"/"en" lose the raw code, so the
                file no longer round-trips into further SDMX key queries.
            max_preview_rows: Max data rows to include inline (default 200).
            save_to_file: Save the full labeled CSV under downloads/ (default True).
        """
        if label_mode not in ("both", "th", "en"):
            raise ValueError(f"label_mode must be 'both', 'th', or 'en' - got {label_mode!r}.")

        app = _app_ctx(ctx)
        client = app.client

        try:
            structure = await _get_structure_cached(app, dataflow_id, version)
        except SdmxClientError as exc:
            raise ValueError(str(exc)) from exc
        resolved_version = structure["version"] or version

        try:
            csv_text = await client.fetch_data_csv(
                dataflow_id, key, resolved_version, start_period, end_period
            )
        except SdmxClientError as exc:
            raise ValueError(str(exc)) from exc

        rows = list(csv.reader(io.StringIO(csv_text)))
        header, data_rows = (rows[0], rows[1:]) if rows else ([], [])

        # Build a code -> (name, name_th) map for every dimension that has
        # a codelist AND actually appears as a column in this CSV (some
        # dimensions may be dropped from the header depending on the key).
        label_maps: dict[str, dict[str, tuple[str, str]]] = {}
        labeled_dims: list[str] = []
        for dim in structure["dimensions"]:
            dim_id = _dget(dim, "id")
            codelist_id = _dget(dim, "codelist_id")
            if not codelist_id or dim_id not in header:
                continue
            codelist_agency = _dget(dim, "codelist_agency")
            try:
                codelist = await _get_codelist_cached(app, codelist_id, codelist_agency, "latest")
            except SdmxClientError:
                continue  # skip labeling this dimension, keep the raw code column
            label_maps[dim_id] = {
                c["id"]: (c.get("name", ""), c.get("name_th", "")) for c in codelist["codes"]
            }
            labeled_dims.append(dim_id)

        # label_mode="both": keep the raw code column and insert
        # "<DIM>_LABEL_EN"/"<DIM>_LABEL_TH" right after it.
        # label_mode="th"/"en": replace the raw code column's value in
        # place with just that language's label (same column name/count,
        # no columns added) - falls back to the raw code if unlabeled.
        new_header: list[str] = []
        ops: list[tuple[str, int, str | None]] = []  # ("copy"|"replace_en"|"replace_th"|"label_en"|"label_th", src_idx, dim_id)
        for idx, col in enumerate(header):
            if col in label_maps and label_mode in ("th", "en"):
                new_header.append(col)
                ops.append((f"replace_{label_mode}", idx, col))
                continue
            new_header.append(col)
            ops.append(("copy", idx, None))
            if col in label_maps and label_mode == "both":
                new_header.append(f"{col}_LABEL_EN")
                ops.append(("label_en", idx, col))
                new_header.append(f"{col}_LABEL_TH")
                ops.append(("label_th", idx, col))

        def build_row(src: list[str]) -> list[str]:
            out = []
            for kind, idx, dim_id in ops:
                code = src[idx] if idx < len(src) else ""
                if kind == "copy":
                    out.append(code)
                    continue
                en, th = label_maps[dim_id].get(code, ("", ""))
                if kind == "label_en":
                    out.append(en)
                elif kind == "label_th":
                    out.append(th)
                elif kind == "replace_en":
                    out.append(en or code)
                elif kind == "replace_th":
                    out.append(th or code)
            return out

        labeled_rows = [build_row(r) for r in data_rows]
        total_rows = len(labeled_rows)
        preview_rows = labeled_rows[:max_preview_rows]
        truncated = total_rows > len(preview_rows)

        out_buf = io.StringIO()
        writer = csv.writer(out_buf)
        if header:
            writer.writerow(new_header)
            writer.writerows(labeled_rows)
        full_csv_text = out_buf.getvalue()

        preview_buf = io.StringIO()
        writer = csv.writer(preview_buf)
        if header:
            writer.writerow(new_header)
            writer.writerows(preview_rows)

        file_path = None
        if save_to_file:
            DOWNLOADS_DIR.mkdir(exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            safe_key = "all" if key == "all" else key.replace("+", "_").replace(".", "-")
            suffix = "labeled" if label_mode == "both" else f"labeled_{label_mode}"
            out_path = DOWNLOADS_DIR / f"{dataflow_id}_{safe_key}_{timestamp}_{suffix}.csv"
            # BOM + newline="", see fetch_data() note above.
            with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
                f.write(full_csv_text)
            file_path = str(out_path.resolve())

        source_url = SdmxClient.build_data_url(
            dataflow_id, key, "csv", start_period, end_period, resolved_version
        )

        return DataFetchLabeledResult(
            dataflow_id=dataflow_id,
            key=key,
            version=resolved_version,
            label_mode=label_mode,
            total_rows=total_rows,
            returned_rows=len(preview_rows),
            truncated=truncated,
            labeled_dimensions=labeled_dims,
            file_path=file_path,
            preview_csv=preview_buf.getvalue(),
            source_url=source_url,
            notes=(
                "source_url points at the raw (unlabeled) SDMX data endpoint - "
                "labels were joined in locally from the cached codelists."
            ),
        )

    @mcp.tool()
    async def fetch_data_dictionary(
        ctx: Context,
        dataflow_id: str,
        version: str = "latest",
        max_preview_rows: int = 200,
        save_to_file: bool = True,
    ) -> DataDictionaryResult:
        """
        Build a data dictionary (codebook) for a dataflow: every dimension
        column, and for each coded dimension every value it can take with
        its English and Thai label. The reference companion to
        fetch_data()/fetch_data_labeled() - use this to understand what a
        dataflow's columns and codes mean before (or without) downloading
        its actual observations.

        One row per (dimension, code). Dimensions with no codelist (e.g.
        TIME_PERIOD, which is free-form) get a single row noting that
        instead of a code list. Uses the same cached codelists as
        get_dimension_codes()/fetch_data_labeled(), so it's fast even for
        large codelists (e.g. AREA has one row per Thai province/district).

        Args:
            dataflow_id: Dataflow id, e.g. "DF_01DI_IND_AGING".
            version: Dataflow version, or "latest" (default).
            max_preview_rows: Max rows to include inline (default 200).
            save_to_file: Save the full dictionary CSV under downloads/ (default True).
        """
        app = _app_ctx(ctx)
        try:
            structure = await _get_structure_cached(app, dataflow_id, version)
        except SdmxClientError as exc:
            raise ValueError(str(exc)) from exc
        resolved_version = structure["version"] or version

        header = ["DIMENSION_ID", "POSITION", "TYPE", "CODELIST_ID", "CODE_ID", "CODE_NAME_EN", "CODE_NAME_TH"]
        dict_rows: list[list[str]] = []
        total_codes = 0
        for dim in structure["dimensions"]:
            dim_id = _dget(dim, "id")
            position = _dget(dim, "position")
            dim_type = _dget(dim, "type")
            codelist_id = _dget(dim, "codelist_id")
            codelist_agency = _dget(dim, "codelist_agency")
            if not codelist_id:
                dict_rows.append([dim_id, str(position), dim_type, "", "", "(free text / not enumerated)", ""])
                continue
            try:
                codelist = await _get_codelist_cached(app, codelist_id, codelist_agency, "latest")
            except SdmxClientError as exc:
                dict_rows.append([dim_id, str(position), dim_type, codelist_id, "", f"(codelist unavailable: {exc})", ""])
                continue
            for c in codelist["codes"]:
                dict_rows.append(
                    [dim_id, str(position), dim_type, codelist_id, c["id"], c.get("name", ""), c.get("name_th", "")]
                )
                total_codes += 1

        total_rows = len(dict_rows)
        preview_rows = dict_rows[:max_preview_rows]
        truncated = total_rows > len(preview_rows)

        out_buf = io.StringIO()
        writer = csv.writer(out_buf)
        writer.writerow(header)
        writer.writerows(dict_rows)
        full_csv_text = out_buf.getvalue()

        preview_buf = io.StringIO()
        writer = csv.writer(preview_buf)
        writer.writerow(header)
        writer.writerows(preview_rows)

        file_path = None
        if save_to_file:
            DOWNLOADS_DIR.mkdir(exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            out_path = DOWNLOADS_DIR / f"{dataflow_id}_{resolved_version}_dictionary_{timestamp}.csv"
            # BOM + newline="", see fetch_data() note above.
            with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
                f.write(full_csv_text)
            file_path = str(out_path.resolve())

        return DataDictionaryResult(
            dataflow_id=dataflow_id,
            version=resolved_version,
            dataflow_name=structure.get("name", ""),
            dataflow_name_th=structure.get("name_th", ""),
            total_dimensions=len(structure["dimensions"]),
            total_codes=total_codes,
            total_rows=total_rows,
            returned_rows=len(preview_rows),
            truncated=truncated,
            file_path=file_path,
            preview_csv=preview_buf.getvalue(),
        )

    @mcp.tool()
    def get_cache_status(ctx: Context) -> dict[str, Any]:
        """
        Snapshot of the local metadata cache: row counts and freshness.

        No network call. Returns {"enabled": false} if the sibling
        stathub-cache folder isn't present on this machine (caching is
        then silently disabled and every tool falls back to live calls).
        """
        cache = _app_ctx(ctx).cache
        if cache is None:
            return {"enabled": False}
        return {"enabled": True, **cache.get_status()}

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
        app = _app_ctx(ctx)
        try:
            codelist = await _get_codelist_cached(app, codelist_id, agency, version)
        except SdmxClientError as exc:
            raise ValueError(str(exc)) from exc

        return CodelistResult(
            codelist_id=codelist["codelist_id"],
            agency_id=codelist["agency_id"],
            version=codelist["version"],
            name=codelist["name"],
            name_th=codelist.get("name_th", ""),
            total_codes=len(codelist["codes"]),
            codes=[CodeItem(**c) for c in codelist["codes"]],
        )
