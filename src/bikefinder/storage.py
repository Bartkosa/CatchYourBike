from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

from bikefinder.config import AppConfig

# UTC wall-clock, same string shape as ``listing_posted_at`` (Subito API + Wallapop normalizer).
_LISTING_DATETIME_DB_FMT = "%Y-%m-%d %H:%M:%S"


def _utc_now_db_string() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime(
        _LISTING_DATETIME_DB_FMT
    )


def _normalize_seen_timestamp_for_db(value: str) -> str | None:
    """Rewrite legacy ISO ``first_seen`` / ``last_seen`` values to match ``listing_posted_at``."""
    s = str(value).strip()
    if not s:
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", s):
        return s
    if "T" not in s and not s.endswith("Z") and not re.search(r"[+-]\d{2}:\d{2}$", s):
        return None
    try:
        normalized = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt.replace(microsecond=0).strftime(_LISTING_DATETIME_DB_FMT)
    except ValueError:
        return None


_LISTINGS_DDL = """
    listing_id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    listing_posted_at TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    image_urls TEXT,
    search_name TEXT,
    location TEXT,
    image_relevance DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    gemini_reason TEXT,
    price TEXT,
    notified INTEGER NOT NULL DEFAULT 0
"""

_CRAWL_PAGE_LOGS_DDL = """
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    search_name TEXT NOT NULL,
    page_url TEXT NOT NULL,
    crawled_at TIMESTAMPTZ NOT NULL,
    already_in_db_count INTEGER NOT NULL,
    added_to_db_count INTEGER NOT NULL,
    before_cutoff_count INTEGER NOT NULL,
    min_listing_posted_at TIMESTAMPTZ,
    max_listing_posted_at TIMESTAMPTZ
"""


@dataclass(frozen=True)
class SourceListingStats:
    """Per-marketplace aggregates derived from ``listing_id`` prefix (``source:…``)."""

    source_id: str
    scraped_last_window: int
    pending_among_last_window: int
    total_in_db: int
    not_awaiting_gemini: int
    pending_gemini_queue: int


@dataclass
class StoredListing:
    listing_id: str
    url: str
    title: str
    listing_posted_at: str | None
    price: str | None
    image_urls: list[str]
    search_name: str | None
    location: str | None
    first_seen: str
    last_seen: str
    image_relevance: float
    gemini_reason: str | None
    notified: int


def _ensure_listings_table(conn: Any) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS listings (
            {_LISTINGS_DDL}
        )
        """
    )

def _migrate_listings_columns(conn: Any) -> None:
    """
    Add missing columns to an existing `listings` table.

    We keep it intentionally simple (no Alembic) since this project is personal.
    """
    rows = conn.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'listings'
          AND table_schema = 'public'
        """
    ).fetchall()
    existing = {str(r["column_name"]) for r in rows}
    want: dict[str, str] = {
        "image_urls": "image_urls TEXT",
        "search_name": "search_name TEXT",
        "location": "location TEXT",
    }
    for col, ddl in want.items():
        if col in existing:
            continue
        conn.execute(f"ALTER TABLE listings ADD COLUMN {ddl}")
    conn.commit()


def _ensure_crawl_page_logs_table(conn: Any) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS crawl_page_logs (
            {_CRAWL_PAGE_LOGS_DDL}
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS crawl_page_logs_crawled_at_idx
        ON crawl_page_logs (crawled_at DESC)
        """
    )


class Storage:
    """Listing history in PostgreSQL."""

    def __init__(self, database_url: str) -> None:
        url = database_url.strip()
        if not url:
            raise ValueError("database_url must be non-empty")
        self._conn = psycopg.connect(url, autocommit=False, row_factory=dict_row)
        _ensure_listings_table(self._conn)
        _ensure_crawl_page_logs_table(self._conn)
        self._conn.commit()
        _migrate_listings_columns(self._conn)
        self._migrate_seen_timestamps_format()
        self._migrate_legacy_subito_listing_ids()

    @classmethod
    def from_config(cls, cfg: AppConfig) -> Storage:
        return cls((cfg.database_url or "").strip())

    def _migrate_seen_timestamps_format(self) -> None:
        rows = self._conn.execute(
            """
            SELECT listing_id, first_seen, last_seen FROM listings
            WHERE first_seen LIKE '%T%' OR first_seen LIKE '%+%' OR first_seen LIKE '%Z'
               OR last_seen LIKE '%T%' OR last_seen LIKE '%+%' OR last_seen LIKE '%Z'
            """
        ).fetchall()
        for row in rows:
            lid = str(row["listing_id"])
            fs = _normalize_seen_timestamp_for_db(str(row["first_seen"]))
            ls = _normalize_seen_timestamp_for_db(str(row["last_seen"]))
            if fs is None and ls is None:
                continue
            new_fs = fs if fs is not None else str(row["first_seen"])
            new_ls = ls if ls is not None else str(row["last_seen"])
            self._conn.execute(
                "UPDATE listings SET first_seen = %s, last_seen = %s WHERE listing_id = %s",
                (new_fs, new_ls, lid),
            )
        self._conn.commit()

    def _migrate_legacy_subito_listing_ids(self) -> None:
        rows = self._conn.execute("SELECT listing_id FROM listings").fetchall()
        for row in rows:
            lid = str(row["listing_id"])
            if ":" in lid or not lid.isdigit():
                continue
            self._conn.execute(
                "UPDATE listings SET listing_id = %s WHERE listing_id = %s",
                (f"subito:{lid}", lid),
            )
        self._conn.commit()

    def _row_get(self, row: Any, key: str, default: Any = None) -> Any:
        try:
            v = row[key]
        except (KeyError, IndexError, TypeError):
            return default
        return v

    def get(self, listing_id: str) -> StoredListing | None:
        row = self._conn.execute(
            "SELECT * FROM listings WHERE listing_id = %s",
            (listing_id,),
        ).fetchone()
        if not row:
            return None
        raw_rel = row["image_relevance"]
        relevance = float(raw_rel) if raw_rel is not None else 0.0
        rgr = self._row_get(row, "gemini_reason")
        gemini_reason = None if rgr is None else str(rgr)
        rp = self._row_get(row, "price")
        price = None if rp is None or (isinstance(rp, str) and not rp.strip()) else str(rp)

        raw_imgs = self._row_get(row, "image_urls")
        image_urls: list[str] = []
        if raw_imgs:
            try:
                parsed = json.loads(str(raw_imgs))
                if isinstance(parsed, list):
                    image_urls = [str(x) for x in parsed if x]
            except json.JSONDecodeError:
                image_urls = []

        return StoredListing(
            listing_id=row["listing_id"],
            url=row["url"],
            title=row["title"],
            listing_posted_at=row["listing_posted_at"],
            price=price,
            image_urls=image_urls,
            search_name=self._row_get(row, "search_name"),
            location=self._row_get(row, "location"),
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            image_relevance=relevance,
            gemini_reason=gemini_reason,
            notified=int(row["notified"]),
        )

    def upsert(
        self,
        listing_id: str,
        url: str,
        title: str,
        listing_posted_at: str | None,
        image_relevance: float,
        gemini_reason: str | None = None,
        price: str | None = None,
        *,
        image_urls: list[str] | None = None,
        search_name: str | None = None,
        location: str | None = None,
    ) -> tuple[bool, StoredListing]:
        now = _utc_now_db_string()
        normalized_rel = float(image_relevance)
        reason_db: str | None = None
        if gemini_reason is not None:
            s = str(gemini_reason).strip()
            reason_db = s if s else None
        price_db: str | None = None
        if price is not None:
            ps = str(price).strip()
            price_db = ps if ps else None

        prev = self.get(listing_id)

        # For metadata fields, default to preserving previous values when callers don't provide them.
        if prev is not None:
            if image_urls is None:
                image_urls = prev.image_urls
            if search_name is None:
                search_name = prev.search_name
            if location is None:
                location = prev.location

        image_urls_db: str | None = None
        if image_urls is not None:
            image_urls_db = json.dumps(image_urls, ensure_ascii=False)

        search_name_db: str | None = None
        if search_name is not None:
            sn = str(search_name).strip()
            search_name_db = sn if sn else None

        location_db: str | None = None
        if location is not None:
            ln = str(location).strip()
            location_db = ln if ln else None

        if prev is None:
            self._conn.execute(
                """
                INSERT INTO listings (
                    listing_id, url, title, listing_posted_at, first_seen, last_seen,
                    image_urls, search_name, location,
                    image_relevance, gemini_reason, price, notified
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0)
                """,
                (
                    listing_id,
                    url,
                    title,
                    listing_posted_at,
                    now,
                    now,
                    image_urls_db,
                    search_name_db,
                    location_db,
                    normalized_rel,
                    reason_db,
                    price_db,
                ),
            )
            self._conn.commit()
            return True, self.get(listing_id)  # type: ignore[return-value]

        self._conn.execute(
            """
            UPDATE listings SET
                url = %s, title = %s, listing_posted_at = %s, last_seen = %s,
                image_urls = %s, search_name = %s, location = %s,
                image_relevance = %s, gemini_reason = %s, price = %s
            WHERE listing_id = %s
            """,
            (
                url,
                title,
                listing_posted_at,
                now,
                image_urls_db,
                search_name_db,
                location_db,
                normalized_rel,
                reason_db,
                price_db,
                listing_id,
            ),
        )
        self._conn.commit()
        return False, self.get(listing_id)  # type: ignore[return-value]

    def mark_notified(self, listing_id: str) -> None:
        self._conn.execute(
            "UPDATE listings SET notified = 1 WHERE listing_id = %s",
            (listing_id,),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def log_crawl_page(
        self,
        *,
        source: str,
        search_name: str,
        page_url: str,
        crawled_at: datetime,
        already_in_db_count: int,
        added_to_db_count: int,
        before_cutoff_count: int,
        min_listing_posted_at: datetime | None,
        max_listing_posted_at: datetime | None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO crawl_page_logs (
                source, search_name, page_url, crawled_at,
                already_in_db_count, added_to_db_count, before_cutoff_count,
                min_listing_posted_at, max_listing_posted_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                source,
                search_name,
                page_url,
                crawled_at,
                int(already_in_db_count),
                int(added_to_db_count),
                int(before_cutoff_count),
                min_listing_posted_at,
                max_listing_posted_at,
            ),
        )
        self._conn.commit()

    def fetch_unscored_listings_batch(
        self,
        *,
        limit: int = 30,
        source_ids: frozenset[str] | None = None,
        search_names: frozenset[str] | None = None,
    ) -> list[StoredListing]:
        """
        Fetch a batch of listings that still need Gemini scoring.

        Contract:
        - ``image_relevance = -1`` means "not scored yet" (with images).
        - ``image_urls`` must be present for Gemini image comparison.

        When ``source_ids`` / ``search_names`` are set (e.g. from filtered config),
        only rows matching those scopes are returned so ``score --only-source``
        behaves like crawl for that marketplace.
        """
        conditions = [
            "image_relevance = -1",
            "image_urls IS NOT NULL",
            "image_urls <> '[]'",
        ]
        params: list[Any] = []

        if source_ids is not None:
            if not source_ids:
                return []
            parts: list[str] = []
            for sid in sorted(source_ids):
                if not sid or not all(c.isalnum() or c == "_" for c in sid):
                    raise ValueError(f"invalid source id for filter: {sid!r}")
                parts.append("listing_id LIKE %s")
                params.append(f"{sid}:%")
            conditions.append("(" + " OR ".join(parts) + ")")

        if search_names is not None:
            if not search_names:
                return []
            placeholders = ",".join(["%s"] * len(search_names))
            conditions.append(f"search_name IN ({placeholders})")
            params.extend(sorted(search_names))

        where_sql = " AND ".join(conditions)
        sql = f"""
            SELECT
                listing_id, url, title, listing_posted_at, price,
                image_urls, search_name, location,
                first_seen, last_seen,
                image_relevance, gemini_reason, notified
            FROM listings
            WHERE {where_sql}
            ORDER BY listing_posted_at DESC
            LIMIT %s
            """
        params.append(int(limit))

        rows = self._conn.execute(sql, params).fetchall()

        out: list[StoredListing] = []
        for row in rows:
            raw_imgs = row["image_urls"]
            image_urls: list[str] = []
            if raw_imgs:
                try:
                    parsed = json.loads(str(raw_imgs))
                    if isinstance(parsed, list):
                        image_urls = [str(x) for x in parsed if x]
                except json.JSONDecodeError:
                    image_urls = []

            rgr = self._row_get(row, "gemini_reason")
            gemini_reason = None if rgr is None else str(rgr)

            out.append(
                StoredListing(
                    listing_id=str(row["listing_id"]),
                    url=str(row["url"]),
                    title=str(row["title"]),
                    listing_posted_at=row["listing_posted_at"],
                    price=row["price"],
                    image_urls=image_urls,
                    search_name=self._row_get(row, "search_name"),
                    location=self._row_get(row, "location"),
                    first_seen=str(row["first_seen"]),
                    last_seen=str(row["last_seen"]),
                    image_relevance=float(row["image_relevance"]),
                    gemini_reason=gemini_reason,
                    notified=int(row["notified"]),
                )
            )

        return out

    def fetch_listing_stats_by_source(self, *, hours: float = 1.0) -> list[SourceListingStats]:
        """
        Listing counts grouped by marketplace id (``split_part(listing_id, ':', 1)``).

        - *scraped in window*: ``first_seen`` is UTC wall time and compared to now−hours.
        - *pending*: ``image_relevance = -1`` with non-empty ``image_urls`` (same as ``score``).
        - *not awaiting Gemini*: ``image_relevance <> -1`` (includes no-image rows at 0.0).
        """
        if hours <= 0:
            raise ValueError("hours must be positive")
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(microsecond=0)
        cutoff_str = cutoff.strftime(_LISTING_DATETIME_DB_FMT)

        rows = self._conn.execute(
            """
            SELECT
              LOWER(split_part(listing_id, ':', 1)) AS source_id,
              COUNT(*) FILTER (
                WHERE first_seen::timestamp >= %s::timestamp
              ) AS scraped_last_window,
              COUNT(*) FILTER (
                WHERE first_seen::timestamp >= %s::timestamp
                  AND image_relevance = -1
                  AND image_urls IS NOT NULL
                  AND btrim(image_urls) <> ''
                  AND btrim(image_urls) <> '[]'
              ) AS pending_among_last_window,
              COUNT(*)::bigint AS total_in_db,
              COUNT(*) FILTER (WHERE image_relevance IS DISTINCT FROM -1)::bigint
                AS not_awaiting_gemini,
              COUNT(*) FILTER (
                WHERE image_relevance = -1
                  AND image_urls IS NOT NULL
                  AND btrim(image_urls) <> ''
                  AND btrim(image_urls) <> '[]'
              )::bigint AS pending_gemini_queue
            FROM listings
            GROUP BY 1
            ORDER BY 1
            """,
            (cutoff_str, cutoff_str),
        ).fetchall()

        out: list[SourceListingStats] = []
        for row in rows:
            sid = str(row["source_id"] or "").strip() or "unknown"
            out.append(
                SourceListingStats(
                    source_id=sid,
                    scraped_last_window=int(row["scraped_last_window"] or 0),
                    pending_among_last_window=int(row["pending_among_last_window"] or 0),
                    total_in_db=int(row["total_in_db"] or 0),
                    not_awaiting_gemini=int(row["not_awaiting_gemini"] or 0),
                    pending_gemini_queue=int(row["pending_gemini_queue"] or 0),
                )
            )
        return out
