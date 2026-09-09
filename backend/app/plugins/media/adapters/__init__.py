"""Media source adapters."""
from .base import (
    LegacyDocument,
    MediaCatalogPage,
    MediaSourceAdapter,
    ResolvedMedia,
    adapter_for,
)
from .rss import MediaNotResolvable, RssMediaAdapter, fingerprint_legacy_document

__all__ = [
    "LegacyDocument",
    "MediaCatalogPage",
    "MediaSourceAdapter",
    "ResolvedMedia",
    "adapter_for",
    "RssMediaAdapter",
    "MediaNotResolvable",
    "fingerprint_legacy_document",
]


def get_adapter_for_format(source_format: str):
    if source_format == "book":
        from .book import BookMediaAdapter

        return BookMediaAdapter
    return RssMediaAdapter