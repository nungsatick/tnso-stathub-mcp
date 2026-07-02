"""
Offline tests for SdmxClient's SDMX-ML parsing logic.

These use httpx.MockTransport to serve canned XML fixtures instead of
hitting the real TNSO API, so they run without network access and verify
the parsing code against realistic SDMX 2.1 structure messages. They do
NOT prove the fixtures match TNSO's actual response shape - see
scripts/smoke_test.py for that.
"""

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import CONFIG  # noqa: E402
from sdmx_client import SdmxClient, SdmxClientError  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _read_fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def make_client(handler) -> SdmxClient:
    """Build an SdmxClient wired to an in-memory MockTransport."""
    client = SdmxClient()
    client._client = httpx.AsyncClient(
        base_url=CONFIG.base_url,
        transport=httpx.MockTransport(handler),
    )
    return client


@pytest.mark.asyncio
async def test_list_dataflows_parses_stubs():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/dataflow/TNSO/all/latest"
        return httpx.Response(200, content=_read_fixture("dataflow_all_stubs.xml"))

    client = make_client(handler)
    try:
        flows = await client.list_dataflows()
    finally:
        await client.aclose()

    assert len(flows) == 2
    assert flows[0]["id"] == "DF_03GARD_PUB"
    assert flows[0]["name"] == "Public Garden Statistics"  # prefers xml:lang="en"
    assert flows[1]["id"] == "DF_05DIS_298_GRP"


@pytest.mark.asyncio
async def test_get_dataflow_structure_parses_dimensions():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/dataflow/TNSO/DF_03GARD_PUB/1.1"
        assert request.url.params["references"] == "all"
        return httpx.Response(200, content=_read_fixture("dataflow_structure.xml"))

    client = make_client(handler)
    try:
        structure = await client.get_dataflow_structure("DF_03GARD_PUB", "1.1")
    finally:
        await client.aclose()

    assert structure["dsd_id"] == "DSD_03GARD_PUB"
    dims = structure["dimensions"]
    assert [d.id for d in dims] == ["GEO", "FREQ", "TIME_PERIOD"]
    assert dims[0].codelist_id == "CL_GEO"
    assert dims[2].type == "TimeDimension"
    assert dims[2].codelist_id is None  # TimeDimension has no codelist enumeration


@pytest.mark.asyncio
async def test_get_dataflow_structure_missing_dataflow_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    client = make_client(handler)
    try:
        with pytest.raises(SdmxClientError):
            await client.get_dataflow_structure("DOES_NOT_EXIST")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_get_codelist_parses_codes_and_search():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/codelist/TNSO/CL_GEO/1.0"
        return httpx.Response(200, content=_read_fixture("codelist_geo.xml"))

    client = make_client(handler)
    try:
        codelist = await client.get_codelist("CL_GEO", "TNSO", "1.0")
    finally:
        await client.aclose()

    assert codelist["name"] == "Geography"
    ids = [c["id"] for c in codelist["codes"]]
    assert ids == ["TH", "BKK", "CNX"]
    assert codelist["codes"][1]["name"] == "Bangkok"


@pytest.mark.asyncio
async def test_probe_data_availability_parses_availableconstraint_success():
    """
    Regression test: KeyValue lives in the "common" namespace (com:), not
    "structure" (str:). An earlier version of probe_data_availability looked
    for str:KeyValue and always found zero matches, silently falling through
    to has_data=False even when /availableconstraint/ succeeded and TNSO had
    real data - confirmed live against DF_01DI_IND_AGING, which returned
    659 CSV data rows despite the buggy code reporting has_data=False.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert "availableconstraint" in request.url.path
        return httpx.Response(200, content=_read_fixture("availableconstraint.xml"))

    client = make_client(handler)
    try:
        result = await client.probe_data_availability("DF_01DI_IND_AGING", "all", "1.0")
    finally:
        await client.aclose()

    assert result["method"] == "availableconstraint"
    assert result["has_data"] is True


@pytest.mark.asyncio
async def test_probe_data_availability_falls_back_to_serieskeysonly():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if "availableconstraint" in request.url.path:
            return httpx.Response(404, text="not implemented")
        assert request.url.path.startswith("/rest/data/TNSO,DF_03GARD_PUB,1.1/all")
        csv_body = "GEO,FREQ,TIME_PERIOD\nTH,A,2022\nTH,A,2023\n"
        return httpx.Response(200, text=csv_body)

    client = make_client(handler)
    try:
        result = await client.probe_data_availability("DF_03GARD_PUB", "all", "1.1")
    finally:
        await client.aclose()

    assert result["method"] == "serieskeysonly_probe"
    assert result["has_data"] is True
    assert result["series_count"] == 2
    assert result["time_period_start"] == "2022"
    assert result["time_period_end"] == "2023"
    assert any("availableconstraint" in c for c in calls)


def test_build_data_url_includes_expected_params():
    url = SdmxClient.build_data_url(
        "DF_03GARD_PUB", key="A..TH.", format_type="csv",
        start_period="2020", end_period="2023", version="1.1",
    )
    assert url.startswith(f"{CONFIG.base_url}/data/TNSO,DF_03GARD_PUB,1.1/A..TH.?")
    assert "format=csv" in url
    assert "startPeriod=2020" in url
    assert "endPeriod=2023" in url


def test_build_data_url_defaults_to_all_key():
    url = SdmxClient.build_data_url("DF_03GARD_PUB")
    assert "/data/TNSO,DF_03GARD_PUB,latest/all?format=csv" == url.split(CONFIG.base_url)[1]


@pytest.mark.asyncio
async def test_fetch_data_csv_returns_raw_body():
    csv_body = "GEO,FREQ,TIME_PERIOD,OBS_VALUE\nTH,A,2022,100\nTH,A,2023,105\n"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/data/TNSO,DF_03GARD_PUB,1.1/all"
        assert request.url.params["format"] == "csv"
        return httpx.Response(200, text=csv_body)

    client = make_client(handler)
    try:
        result = await client.fetch_data_csv("DF_03GARD_PUB", "all", "1.1")
    finally:
        await client.aclose()

    assert result == csv_body


@pytest.mark.asyncio
async def test_fetch_data_csv_includes_period_params():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["startPeriod"] == "2020"
        assert request.url.params["endPeriod"] == "2023"
        return httpx.Response(200, text="GEO\nTH\n")

    client = make_client(handler)
    try:
        await client.fetch_data_csv(
            "DF_03GARD_PUB", "all", "1.1", start_period="2020", end_period="2023"
        )
    finally:
        await client.aclose()
