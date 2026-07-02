# TNSO StatHub MCP

A Model Context Protocol (MCP) server that lets AI agents (Claude, ChatGPT, and
other MCP clients) discover and query official Thai statistics published by
the **National Statistical Office of Thailand (NSO)** through its SDMX 2.1
REST gateway, [StatHub](https://stathub.nso.go.th/).

It uses a **progressive discovery** pattern: instead of dumping an entire SDMX
metadata document (which can exceed 100KB) into an LLM's context window, it
exposes small, composable tools that mirror how a person would explore the
data - find a dataflow, inspect its dimensions, look up a code, check that
data actually exists, then build a query URL.

This project targets **TNSO only** (single provider, no configuration
needed). It was designed after studying
[Baffelan/sdmx-mcp-gateway](https://github.com/Baffelan/sdmx-mcp-gateway), a
multi-provider SDMX MCP gateway, but is a from-scratch, simplified
implementation - see [Credits](#credits).

## Status

✅ **Verified against live TNSO data.** `scripts/smoke_test.py` has been run
against the real API: it found 809 real dataflows, parsed a real dataflow's
8 dimensions (including `TIME_PERIOD`), pulled a real 109-code codelist with
correct labels, and `build_data_url()`'s output was confirmed to return real
CSV data (659 rows) when fetched directly. One bug turned up and was fixed
along the way - see below.

If you pull a newer version of this server or modify `sdmx_client.py`,
re-run the smoke test to confirm nothing regressed:

```bash
uv sync   # or: pip install -r requirements.txt
python scripts/smoke_test.py
```

This hits the real endpoint, prints what it finds at each step, and flags
anything that doesn't parse as expected. If something fails, the error
message will point at the specific request/XPath to fix in `sdmx_client.py`
- see [Troubleshooting](#troubleshooting).

**Known-fixed issue:** `get_data_availability` initially always reported
`has_data=False` even for dataflows with real data, because the code looked
for the SDMX `<KeyValue>` element in the wrong XML namespace (`str:` instead
of `com:`) when parsing `/availableconstraint/` responses. Fixed, and
covered by a regression test (`test_probe_data_availability_parses_availableconstraint_success`
in `tests/test_sdmx_client.py`).

## What this server talks to

| Component | URL | Role |
|---|---|---|
| Data / Structure REST API | `https://ns2-stathub.nso.go.th/rest` | The SDMX 2.1 REST endpoint this server queries (public, no API key required) |
| Metadata Registry | `https://sdmx.nso.go.th/FusionMetadataRegistry/sdmx/v2` | Alternative structure-only endpoint (Fusion Metadata Registry), kept for reference |
| Public portal | `https://stathub.nso.go.th/` | Human-facing data browser (not an API) |
| Agency ID | `TNSO` | Used in all query paths |

## Tools

| Tool | Description |
|---|---|
| `list_dataflows` | Find dataflows by keyword |
| `get_dataflow_structure` | Get a dataflow's dimensions and which codelist backs each one |
| `get_dimension_codes` | Browse/search codes for one dimension |
| `get_data_availability` | Check whether data exists for a key before querying it |
| `validate_query` | Validate a dataflow id / SDMX key's syntax (no network call) |
| `build_data_url` | Construct a ready-to-fetch data URL (no network call) |
| `fetch_data` | Download the actual data as CSV - saves to `downloads/` and returns a preview inline |
| `get_codelist` | Browse a codelist directly by id |

## Caching (optional, Phase 1)

`list_dataflows`, `get_dataflow_structure`, `get_dimension_codes`, and
`get_codelist` transparently read through a local SQLite cache if a
sibling `stathub-cache/` folder is present next to this repo (i.e.
`../stathub-cache` relative to this project). Metadata like dataflow
lists, dimensions, and codelists rarely changes, so cached results are
served directly for 7 days before a fresh TNSO call is made.

This cache is deliberately **not** part of this repo - it holds a
regenerable SQLite database file (local, machine-specific data), not
source code, so it lives in its own folder that never needs to be
committed or published. If that folder doesn't exist (e.g. a fresh clone
of just this repo from GitHub), caching is silently disabled and every
tool call goes straight to TNSO, exactly as it did before this feature
existed - nothing extra to configure.

`get_data_availability` and `fetch_data` are intentionally not cached -
availability/data changes more often than metadata, and `fetch_data`
already writes its own CSV output to `downloads/`.

See `cache_integration.py` for the integration point, and the sibling
`stathub-cache/README.md` (if you have that folder) for how the cache
itself works.

## Resources

- `sdmx://info` - endpoint URLs and auth status
- `sdmx://formats/guide` - data format comparison (csv / json / xml)
- `sdmx://syntax/guide` - SDMX key syntax quick reference

## Prompts

- `discovery_guide` - step-by-step workflow for finding data on a topic
- `troubleshooting_guide` - common failure modes and fixes

## Installation

Requires Python 3.12+.

```bash
git clone https://github.com/<your-username>/tnso-stathub-mcp.git
cd tnso-stathub-mcp
uv sync                        # recommended
# or: pip install -r requirements.txt
```

## Running the server

```bash
# STDIO - for local MCP clients (Claude Code, Claude Desktop, Cursor, ...)
uv run python main_server.py

# Streamable HTTP - for remote clients / ChatGPT (requires public HTTPS)
uv run python main_server.py --transport http --port 8000

# MCP Inspector (browser UI) - useful while developing
uv run mcp dev ./main_server.py
```

CLI options: `python main_server.py --help`

## Connecting an AI client

### Claude Code

```bash
claude mcp add --transport stdio tnso-stathub -- uv run --directory /path/to/tnso-stathub-mcp python main_server.py
```

Or, for a server you've deployed remotely over HTTP:

```bash
claude mcp add --transport http tnso-stathub https://<your-domain>/mcp
```

### Claude Desktop

Edit your config file (Windows: `%APPDATA%\Claude\claude_desktop_config.json`,
macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "tnso-stathub": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/tnso-stathub-mcp", "python", "main_server.py"]
    }
  }
}
```

Restart Claude Desktop after saving.

### ChatGPT (Plus / Pro / Business / Enterprise)

ChatGPT connects to MCP servers via **Apps & Connectors**, and only over
**Streamable HTTP + HTTPS** (a local STDIO server won't work). Deploy the
server first (see [Deployment](#deployment)), then:

1. Settings → Apps → Advanced settings → enable **Developer mode**.
2. Apps → **Create app**.
3. Enter your server URL (e.g. `https://<your-domain>/mcp`).
4. Save and enable the connector in a chat.

Plus/Pro accounts get read-only connectors, which is all this server needs -
every tool here only reads data.

### Other clients (Cursor, Zed, OpenCode, Codex CLI)

Use the same `command`/`args` shown above for Claude Code/Desktop, in each
client's own config format.

## Deployment

A `Dockerfile` is included:

```bash
docker build -t tnso-stathub-mcp .
docker run -p 8000:8000 tnso-stathub-mcp
```

Deploys the same way to Railway, Fly.io, or any container platform - bind to
`0.0.0.0` and read `PORT` from the environment (already wired up in
`main_server.py`).

## Configuration

All settings are optional environment variables - see `.env.example`.
Defaults point at the public TNSO endpoints listed above; no API key is
required today. If NSO adds authentication later, set `TNSO_API_KEY` and it
will be sent via the `Ocp-Apim-Subscription-Key` header (adjust in
`config.py`/`sdmx_client.py` if NSO uses a different header).

## Usage example

```
list_dataflows(keywords=["population"])
# -> DF_03GARD_PUB, DF_05DIS_298_GRP, ... (illustrative ids - run the tool to see real ones)

get_dataflow_structure("DF_03GARD_PUB")
# -> dimensions: GEO, FREQ, TIME_PERIOD (codelist ids per dimension)

get_dimension_codes("DF_03GARD_PUB", "GEO", search_term="bangkok")
# -> BKK: Bangkok

get_data_availability("DF_03GARD_PUB", key="A..BKK.")
# -> has_data: true/false, time range if available

build_data_url("DF_03GARD_PUB", key="A..BKK.", format_type="csv")
# -> https://ns2-stathub.nso.go.th/rest/data/TNSO,DF_03GARD_PUB,latest/A..BKK.?format=csv

fetch_data("DF_03GARD_PUB", key="A..BKK.")
# -> downloads the real CSV, saves it to downloads/DF_03GARD_PUB_A..BKK._<timestamp>.csv,
#    and returns a preview (first 200 rows by default) plus total_rows/truncated
```

`fetch_data` actually retrieves the data (unlike `build_data_url`, which only
builds the URL) and, since this server runs locally, writes the full CSV to
a `downloads/` folder next to the project so it can be opened directly in
Excel or loaded with pandas. Only a bounded preview is returned inline in
the tool result, to avoid flooding the conversation with a large dataset.

## Testing

```bash
# Offline unit tests (validation helpers + XML parsing against fixtures) - no network needed
uv run pytest tests/ -v

# Live smoke test against the real TNSO API - run this once after cloning
uv run python scripts/smoke_test.py
```

## Troubleshooting

- **`scripts/smoke_test.py` reports 0 dataflows or a parse warning**: TNSO's
  actual XML likely differs slightly from the fixtures this was built
  against. Save the raw response (`curl` the URL printed in the error) and
  compare it to `tests/fixtures/*.xml`, then adjust the XPath expressions in
  `sdmx_client.py` (the `qname()` helper builds namespaced tag lookups; cross-
  references use a bare unqualified `<Ref>` element per the SDMX-ML 2.1
  schema).
- **404 on `get_dataflow_structure`**: dataflow ids are case-sensitive; get
  the exact id from `list_dataflows()`.
- **`get_data_availability` always falls back to `serieskeysonly_probe`**:
  expected - TNSO likely doesn't implement the optional
  `/availableconstraint/` resource. The fallback is a standard, always-
  available SDMX 2.1 data query, just slightly heavier.

## Credits

Architecture and progressive-discovery approach informed by
[Baffelan/sdmx-mcp-gateway](https://github.com/Baffelan/sdmx-mcp-gateway)
(MIT licensed). This is an independent, from-scratch implementation scoped
to a single provider (TNSO) rather than a fork.

## License

MIT - see [LICENSE](LICENSE).
