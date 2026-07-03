"""
Minimal async SDMX 2.1 REST client for the TNSO StatHub endpoint.

Scope is intentionally narrow: this client only implements what the MCP
tools in tools/sdmx_tools.py need (dataflow discovery, DSD/dimension
inspection, codelist browsing, a best-effort data-availability probe, and
data-URL construction). It follows the SDMX 2.1 REST API specification
(https://github.com/sdmx-twg/sdmx-rest) and was written against the
publicly documented StatHub endpoint:

    https://ns2-stathub.nso.go.th/rest

IMPORTANT: this client has not been exercised against live TNSO data from
this environment (the development sandbox used to write it has no outbound
network access to *.nso.go.th). Run `python scripts/smoke_test.py` after
cloning, from a machine with normal internet access, before relying on it -
see README.md "Verifying against the live API".
"""

from __future__ import annotations

import csv
import io
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

import httpx

from config import CONFIG, get_api_key
from utils import qname

logger = logging.getLogger(__name__)

STRUCTURE_ACCEPT = "application/vnd.sdmx.structure+xml;version=2.1"


class SdmxClientError(Exception):
    """Raised when the TNSO SDMX API returns an error or unparsable response."""


@dataclass
class DimensionInfo:
    id: str
    position: int
    type: str
    codelist_id: str | None
    codelist_agency: str | None


class SdmxClient:
    """Thin async wrapper around httpx for TNSO's SDMX 2.1 REST endpoint."""

    def __init__(self) -> None:
        headers = {"Accept": STRUCTURE_ACCEPT}
        api_key = get_api_key()
        if api_key:
            headers[CONFIG.api_key_header] = api_key
        self._client = httpx.AsyncClient(
            base_url=CONFIG.base_url,
            timeout=CONFIG.timeout_seconds,
            headers=headers,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Low-level HTTP + XML helpers
    # ------------------------------------------------------------------

    async def _get_xml(self, path: str, params: dict[str, Any] | None = None) -> ET.Element:
        try:
            resp = await self._client.get(path, params=params)
        except httpx.RequestError as exc:
            raise SdmxClientError(f"Network error calling TNSO API at {path}: {exc}") from exc

        if resp.status_code == 404:
            raise SdmxClientError(
                f"Not found (404) for {path} - check the dataflow/codelist id and version."
            )
        if resp.status_code >= 400:
            raise SdmxClientError(
                f"TNSO API returned HTTP {resp.status_code} for {path}: {resp.text[:300]}"
            )

        try:
            return ET.fromstring(resp.content)
        except ET.ParseError as exc:
            raise SdmxClientError(f"Could not parse XML response from {path}: {exc}") from exc

    async def _get_text(self, path: str, params: dict[str, Any] | None = None) -> str:
        try:
            resp = await self._client.get(path, params=params)
        except httpx.RequestError as exc:
            raise SdmxClientError(f"Network error calling TNSO API at {path}: {exc}") from exc
        if resp.status_code >= 400:
            raise SdmxClientError(
                f"TNSO API returned HTTP {resp.status_code} for {path}: {resp.text[:300]}"
            )
        return resp.text

    @staticmethod
    def _all_names(parent: ET.Element) -> dict[str, str]:
        """Collect every localized com:Name on a structure element, keyed by
        xml:lang (e.g. {"en": "...", "th": "..."})."""
        names: dict[str, str] = {}
        for name_el in parent.findall(qname("com", "Name")):
            lang = name_el.get(f"{{http://www.w3.org/XML/1998/namespace}}lang", "")
            if name_el.text:
                names[lang] = name_el.text
        return names

    @classmethod
    def _best_name(cls, parent: ET.Element, prefer: tuple[str, ...] = ("en", "th")) -> str:
        """Pick the best localized com:Name from a structure element."""
        names = cls._all_names(parent)
        for lang in prefer:
            if lang in names:
                return names[lang]
        return next(iter(names.values()), "")

    # ------------------------------------------------------------------
    # Dataflows
    # ------------------------------------------------------------------

    async def list_dataflows(self) -> list[dict[str, str]]:
        """Return all dataflow stubs (id, agency, version, name, description)."""
        root = await self._get_xml(
            f"/dataflow/{CONFIG.agency_id}/all/latest",
            params={"detail": "allstubs"},
        )
        results = []
        for df in root.iter(qname("str", "Dataflow")):
            names = self._all_names(df)
            results.append(
                {
                    "id": df.get("id", ""),
                    "agency": df.get("agencyID", CONFIG.agency_id),
                    "version": df.get("version", "1.0"),
                    "name": self._best_name(df),
                    "name_th": names.get("th", ""),
                    "description": "",
                }
            )
        return results

    async def get_dataflow_structure(
        self, dataflow_id: str, version: str = "latest"
    ) -> dict[str, Any]:
        """Fetch a dataflow together with its DSD and dimension list."""
        root = await self._get_xml(
            f"/dataflow/{CONFIG.agency_id}/{dataflow_id}/{version}",
            params={"references": "all"},
        )

        df_el = root.find(f".//{qname('str', 'Dataflow')}")
        if df_el is None:
            raise SdmxClientError(f"Dataflow '{dataflow_id}' not found in TNSO structure response.")

        # Cross-references use a bare, unqualified <Ref> element in SDMX-ML 2.1
        # (not namespaced under str: or com:), so we match on local tag name.
        dsd_ref_el = df_el.find(f"{qname('str', 'Structure')}/Ref")
        if dsd_ref_el is None:
            dsd_ref_el = df_el.find(".//Ref")
        dsd_id = dsd_ref_el.get("id") if dsd_ref_el is not None else None

        dimensions: list[DimensionInfo] = []
        dsd_el = None
        if dsd_id is not None:
            for candidate in root.iter(qname("str", "DataStructure")):
                if candidate.get("id") == dsd_id:
                    dsd_el = candidate
                    break

        if dsd_el is not None:
            dim_list_el = dsd_el.find(f".//{qname('str', 'DimensionList')}")
            if dim_list_el is not None:
                for i, dim_el in enumerate(dim_list_el):
                    tag = dim_el.tag.split("}")[-1]
                    if tag not in ("Dimension", "TimeDimension"):
                        continue
                    ref_el = dim_el.find(f".//{qname('str', 'Enumeration')}/Ref")
                    dimensions.append(
                        DimensionInfo(
                            id=dim_el.get("id", ""),
                            position=int(dim_el.get("position", i)),
                            type=tag,
                            codelist_id=ref_el.get("id") if ref_el is not None else None,
                            codelist_agency=ref_el.get("agencyID") if ref_el is not None else None,
                        )
                    )
                dimensions.sort(key=lambda d: d.position)

        return {
            "dataflow_id": dataflow_id,
            "agency_id": CONFIG.agency_id,
            "version": df_el.get("version", version),
            "name": self._best_name(df_el),
            "name_th": self._all_names(df_el).get("th", ""),
            "dsd_id": dsd_id,
            "dimensions": dimensions,
        }

    # ------------------------------------------------------------------
    # Codelists
    # ------------------------------------------------------------------

    async def get_codelist(
        self, codelist_id: str, agency: str | None = None, version: str = "latest"
    ) -> dict[str, Any]:
        agency = agency or CONFIG.agency_id
        root = await self._get_xml(f"/codelist/{agency}/{codelist_id}/{version}")
        cl_el = root.find(f".//{qname('str', 'Codelist')}")
        if cl_el is None:
            raise SdmxClientError(f"Codelist '{codelist_id}' not found.")

        codes = []
        for code_el in cl_el.findall(qname("str", "Code")):
            code_names = self._all_names(code_el)
            codes.append(
                {
                    "id": code_el.get("id", ""),
                    "name": self._best_name(code_el),
                    "name_th": code_names.get("th", ""),
                }
            )

        return {
            "codelist_id": codelist_id,
            "agency_id": agency,
            "version": cl_el.get("version", version),
            "name": self._best_name(cl_el),
            "name_th": self._all_names(cl_el).get("th", ""),
            "codes": codes,
        }

    # ------------------------------------------------------------------
    # Data availability (best-effort, two strategies)
    # ------------------------------------------------------------------

    async def probe_data_availability(
        self, dataflow_id: str, key: str = "all", version: str = "latest"
    ) -> dict[str, Any]:
        """
        Best-effort check for whether data exists for a given key.

        Tries the SDMX 2.1 `/availableconstraint/` resource first (cheap,
        purpose-built for this). If TNSO doesn't support it (common - many
        national SDMX registries only implement the core Data/Structure
        resources), falls back to a `serieskeysonly` data query, which is
        supported by any compliant SDMX 2.1 data endpoint.
        """
        flow_ref = f"{CONFIG.agency_id},{dataflow_id},{version}"

        try:
            root = await self._get_xml(f"/availableconstraint/{flow_ref}/{key}/all")
            # KeyValue (and Value) live in the "common" namespace, not
            # "structure" - they're shared across many SDMX-ML structures.
            # Confirmed against live TNSO responses: ContentConstraint and
            # CubeRegion are str:, but the KeyValue entries inside are com:.
            key_values = list(root.iter(qname("com", "KeyValue")))
            return {
                "method": "availableconstraint",
                "has_data": len(key_values) > 0,
                "series_count": None,
                "time_period_start": None,
                "time_period_end": None,
                "notes": "Derived from /availableconstraint/.",
            }
        except SdmxClientError as exc:
            logger.info("availableconstraint unavailable for %s (%s); falling back to serieskeysonly", dataflow_id, exc)

        try:
            text = await self._get_text(
                f"/data/{flow_ref}/{key}",
                params={
                    "detail": "serieskeysonly",
                    "format": "csv",
                    "dimensionAtObservation": "AllDimensions",
                },
            )
        except SdmxClientError as exc:
            return {
                "method": "serieskeysonly_probe",
                "has_data": False,
                "series_count": 0,
                "time_period_start": None,
                "time_period_end": None,
                "notes": f"No data found or query failed: {exc}",
            }

        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        time_values = sorted({r["TIME_PERIOD"] for r in rows if r.get("TIME_PERIOD")})

        return {
            "method": "serieskeysonly_probe",
            "has_data": len(rows) > 0,
            "series_count": len(rows),
            "time_period_start": time_values[0] if time_values else None,
            "time_period_end": time_values[-1] if time_values else None,
            "notes": "Derived from a detail=serieskeysonly data query.",
        }

    async def fetch_data_csv(
        self,
        dataflow_id: str,
        key: str = "all",
        version: str = "latest",
        start_period: str | None = None,
        end_period: str | None = None,
    ) -> str:
        """
        Download the actual observation data for a dataflow as SDMX-CSV.

        Unlike probe_data_availability (which is designed to be cheap and
        only check whether data exists), this performs a full data query
        and returns the complete CSV body - can be large for wide keys.
        """
        flow_ref = f"{CONFIG.agency_id},{dataflow_id},{version}"
        params: dict[str, Any] = {"format": "csv"}
        if start_period:
            params["startPeriod"] = start_period
        if end_period:
            params["endPeriod"] = end_period
        return await self._get_text(f"/data/{flow_ref}/{key}", params=params)

    @staticmethod
    def build_data_url(
        dataflow_id: str,
        key: str = "all",
        format_type: str = "csv",
        start_period: str | None = None,
        end_period: str | None = None,
        version: str = "latest",
    ) -> str:
        """Construct a ready-to-fetch data URL (no network call)."""
        flow_ref = f"{CONFIG.agency_id},{dataflow_id},{version}"
        params = {"format": format_type}
        if start_period:
            params["startPeriod"] = start_period
        if end_period:
            params["endPeriod"] = end_period
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{CONFIG.base_url}/data/{flow_ref}/{key}?{query}"
