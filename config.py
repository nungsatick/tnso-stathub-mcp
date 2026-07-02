"""
Configuration for the TNSO StatHub MCP server.

This server targets a single SDMX 2.1 REST endpoint: the Thai National
Statistical Office's StatHub gateway. Unlike multi-provider gateways, all
settings live in one place and can be overridden via environment variables
(see .env.example) without touching code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class TnsoConfig:
    """Static configuration for the TNSO SDMX endpoint."""

    name: str = "National Statistical Office of Thailand (StatHub)"
    agency_id: str = "TNSO"

    # SDMX 2.1 REST data + structure API (confirmed reachable, returns
    # application/vnd.sdmx.structure+xml; version=2.1 for structure queries).
    base_url: str = "https://ns2-stathub.nso.go.th/rest"

    # Fusion Metadata Registry - alternative structure-only endpoint. Some
    # deployments split "structure" (registry) and "data" (dissemination)
    # across hosts; both are SDMX 2.1 REST compliant and interchangeable for
    # structure queries. Kept here for reference / troubleshooting.
    registry_base_url: str = "https://sdmx.nso.go.th/FusionMetadataRegistry/sdmx/v2"

    # Human-facing portal (not an API endpoint - useful for citing sources).
    portal_url: str = "https://stathub.nso.go.th/"

    # Request timeout in seconds for outbound HTTP calls.
    timeout_seconds: float = 30.0

    # No API key/token is required for ns2-stathub.nso.go.th/rest (verified
    # public access). If NSO introduces authentication later, set
    # TNSO_API_KEY and it will be sent as an "Ocp-Apim-Subscription-Key"
    # header (a common APIM convention also used by other national SDMX
    # gateways) - adjust the header name in sdmx_client.py if NSO uses a
    # different scheme.
    api_key_env: str = "TNSO_API_KEY"
    api_key_header: str = "Ocp-Apim-Subscription-Key"


def get_config() -> TnsoConfig:
    """Build the active configuration, applying environment overrides."""
    return TnsoConfig(
        base_url=os.getenv("TNSO_BASE_URL", TnsoConfig.base_url),
        registry_base_url=os.getenv("TNSO_REGISTRY_BASE_URL", TnsoConfig.registry_base_url),
        agency_id=os.getenv("TNSO_AGENCY_ID", TnsoConfig.agency_id),
        timeout_seconds=float(os.getenv("TNSO_TIMEOUT_SECONDS", TnsoConfig.timeout_seconds)),
    )


CONFIG = get_config()


def get_api_key() -> str | None:
    """Read the optional API key from the environment, if NSO requires one."""
    return os.getenv(CONFIG.api_key_env)
