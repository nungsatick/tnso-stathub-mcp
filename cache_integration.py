"""
Optional integration with the local stathub-cache SQLite cache (Phase 1 of
the caching plan).

stathub-cache lives as a *sibling* folder next to this repo
(../stathub-cache relative to this file), deliberately kept outside
tnso-stathub-mcp's git history: it holds a local SQLite database that's
regenerated from live TNSO calls, so it doesn't belong in version control
or a published repo - see stathub-cache/README.md.

If that folder isn't present - e.g. someone clones only this repo from
GitHub - caching is silently disabled and every tool falls back to a live
TNSO request on every call, exactly as before Phase 1. Nothing here is
required for the server to work.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).resolve().parent.parent / "stathub-cache"
_DB_PATH = _CACHE_DIR / "tnso_cache.db"

# TTLs (seconds). Dataflow lists/structures/codelists change rarely, so a
# generous week-long TTL keeps most reads local. get_data_availability and
# fetch_data are intentionally NOT cached in Phase 1 - see stathub-cache/README.md.
DATAFLOWS_TTL = 7 * 24 * 60 * 60
STRUCTURE_TTL = 7 * 24 * 60 * 60
CODELIST_TTL = 7 * 24 * 60 * 60

_store: Any | None = None
_load_attempted = False


def get_cache() -> Any | None:
    """Return a shared TnsoCacheStore instance, or None if the sibling
    stathub-cache folder isn't present or fails to load. Safe to call
    repeatedly - the store is only constructed once per process."""
    global _store, _load_attempted
    if _load_attempted:
        return _store
    _load_attempted = True

    if not _CACHE_DIR.is_dir():
        logger.info("stathub-cache folder not found at %s - caching disabled.", _CACHE_DIR)
        return None

    if str(_CACHE_DIR) not in sys.path:
        sys.path.insert(0, str(_CACHE_DIR))
    try:
        from cache_store import TnsoCacheStore  # type: ignore  # noqa: E402

        _store = TnsoCacheStore(_DB_PATH)
        logger.info("stathub-cache loaded from %s - caching enabled.", _DB_PATH)
    except Exception:
        logger.exception("stathub-cache present but failed to load - caching disabled.")
        _store = None
    return _store
