#!/usr/bin/env python3
"""
Live smoke test against the real TNSO StatHub API.

This project was developed in a sandboxed environment without outbound
network access to *.nso.go.th, so the SDMX client logic could not be
exercised against live data before publishing. Run this script from a
machine with normal internet access as the first thing you do after
cloning, to confirm the client's XML parsing matches what TNSO actually
returns - and to get real dataflow ids to try the MCP tools with.

Usage:
    uv run python scripts/smoke_test.py
    # or
    python scripts/smoke_test.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# File-relative, not CWD-relative: works no matter where this script is
# invoked from (repo root, scripts/, an IDE's "Run" button, etc.).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sdmx_client import SdmxClient, SdmxClientError  # noqa: E402


async def main() -> int:
    client = SdmxClient()
    exit_code = 0
    try:
        print("1) Listing dataflows (GET /dataflow/TNSO/all/latest)...")
        try:
            flows = await client.list_dataflows()
            print(f"   OK - found {len(flows)} dataflows.")
            for f in flows[:5]:
                print(f"     - {f['id']} ({f['version']}): {f['name']}")
            if not flows:
                print("   WARNING: zero dataflows returned - check detail= param or agency id.")
                exit_code = 1
        except SdmxClientError as exc:
            print(f"   FAILED: {exc}")
            print("   -> Cannot continue with steps 2-3 without a real dataflow id.")
            return 1

        sample_id = flows[0]["id"]
        sample_version = flows[0]["version"]

        print(f"\n2) Fetching structure for '{sample_id}' (GET /dataflow/TNSO/{sample_id}/{sample_version}?references=all)...")
        try:
            structure = await client.get_dataflow_structure(sample_id, sample_version)
            print(f"   OK - DSD: {structure['dsd_id']}, dimensions: {[d.id for d in structure['dimensions']]}")
            if not structure["dimensions"]:
                print("   WARNING: no dimensions parsed - check DSD/DimensionList XPath in sdmx_client.py.")
                exit_code = 1
        except SdmxClientError as exc:
            print(f"   FAILED: {exc}")
            exit_code = 1
            return exit_code

        dim_with_codelist = next((d for d in structure["dimensions"] if d.codelist_id), None)
        if dim_with_codelist:
            print(f"\n3) Fetching codelist '{dim_with_codelist.codelist_id}' for dimension '{dim_with_codelist.id}'...")
            try:
                codelist = await client.get_codelist(dim_with_codelist.codelist_id, dim_with_codelist.codelist_agency)
                print(f"   OK - {len(codelist['codes'])} codes. First few: {[c['id'] for c in codelist['codes'][:5]]}")
            except SdmxClientError as exc:
                print(f"   FAILED: {exc}")
                exit_code = 1
        else:
            print("\n3) Skipped - no dimension with an enumerated codelist found on the sample dataflow.")

        print(f"\n4) Probing data availability for '{sample_id}' key='all'...")
        try:
            avail = await client.probe_data_availability(sample_id, "all", sample_version)
            print(f"   OK - method={avail['method']} has_data={avail['has_data']} notes={avail['notes']}")
        except SdmxClientError as exc:
            print(f"   FAILED: {exc}")
            exit_code = 1

        print(f"\n5) Building a data URL for '{sample_id}'...")
        url = SdmxClient.build_data_url(sample_id, key="all", format_type="csv", version=sample_version)
        print(f"   {url}")

    finally:
        await client.aclose()

    print("\nDone." if exit_code == 0 else "\nDone, with warnings/failures above - see README troubleshooting.")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
