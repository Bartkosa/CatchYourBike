from __future__ import annotations

from dataclasses import replace

from bikefinder.models import Listing


def with_source_prefix(listing: Listing, source_id: str) -> Listing:
    """Ensure ``listing_id`` is ``{source_id}:{native_id}`` and ``source`` is set."""
    sid = source_id.strip().lower()
    prefix = f"{sid}:"
    if listing.listing_id.startswith(prefix):
        return replace(listing, source=sid)
    return replace(
        listing,
        listing_id=f"{prefix}{listing.listing_id}",
        source=sid,
    )
