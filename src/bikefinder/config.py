from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator, model_validator


class SearchEntry(BaseModel):
    name: str = "default"
    url: str = ""
    source: str = "subito"
    ebay_marketplace_ids: list[str] = Field(default_factory=list)
    ebay_location_countries: list[str] = Field(default_factory=list)
    ebay_location_hub_marketplace_id: str = "EBAY_IT"
    # None / omitted → use AppConfig.ebay_default_query. Explicit "" → Browse API
    # category-only search (no `q`; requires ebay_category_ids or ebay_default_category_ids).
    ebay_query: str | None = None
    ebay_category_ids: str | None = None
    ebay_buying_options: str | None = None

    @field_validator("source")
    @classmethod
    def _validate_source(cls, v: str) -> str:
        key = (v or "subito").strip().lower()
        from bikefinder.sources import LISTING_SOURCES

        if key not in LISTING_SOURCES:
            known = ", ".join(sorted(LISTING_SOURCES))
            raise ValueError(f"Unknown source {key!r}. Known: {known}")
        return key

    @field_validator("ebay_location_countries", mode="before")
    @classmethod
    def _normalize_location_countries(cls, v: object) -> list[str]:
        if v is None:
            return []
        if not isinstance(v, list):
            raise TypeError("ebay_location_countries must be a list of country codes")
        out: list[str] = []
        for item in v:
            if not isinstance(item, str):
                raise TypeError("ebay_location_countries entries must be strings")
            s = item.strip().upper()
            if len(s) != 2:
                raise ValueError(
                    f"ebay_location_countries must be 2-letter ISO codes, got {item!r}"
                )
            out.append(s)
        return out

    @model_validator(mode="after")
    def _url_required_unless_ebay(self) -> SearchEntry:
        src = self.source.strip().lower()
        if src != "ebay" and not (self.url or "").strip():
            raise ValueError(
                f"search {self.name!r}: url is required when source is {self.source!r}"
            )
        return self


class AppConfig(BaseModel):
    searches: list[SearchEntry]
    max_pages_per_search: int = Field(default=2, ge=1, le=500)
    delay_seconds: float = Field(default=2.0, ge=0.0)
    reference_images: list[str] = Field(default_factory=list)
    gemini_model: str = "gemini-2.5-pro"
    gemini_threshold: float = Field(default=0.72, ge=0.0, le=1.0)
    # Second Telegram bot (optional env): alert when score > this value OR title contains substring.
    high_confidence_score_gt: float = Field(default=0.90, ge=0.0, le=1.0)
    high_confidence_title_substring: str = "kross"
    # With title substring match, score must exceed this (title alone is not enough).
    high_confidence_title_min_score: float = Field(default=0.6, ge=0.0, le=1.0)
    max_listing_images_for_gemini: int = Field(default=8, ge=1, le=16)
    gemini_timeout_seconds: float = Field(default=90.0, ge=5.0, le=300.0)
    # Extra attempts after httpx timeouts on generateContent; gemini_compare lengthens timeout each retry (cap 300s).
    gemini_generate_retries: int = Field(default=3, ge=0, le=10)
    # Upload images to GCS and send gs:// URIs to Vertex Gemini.
    # If this is disabled, prompts use inline image bytes.
    gemini_use_files_api: bool = True
    vertex_project_id: str = ""
    vertex_location: str = "global"
    vertex_gcs_bucket: str = ""
    min_listing_date: str = "2026-03-19"
    # One-shot mode flag:
    # - `backfill: true` enables "fresh-fill": paginate newest-first until the date frontier
    #   (first listing with `posted_at < min_listing_date`), or until the SERP ends.
    # - CLI `--backfill` ORs with this flag for both `run` and `crawl` (`--watch` forbids backfill on `run`).
    backfill: bool = False
    # PostgreSQL URL (also set from DATABASE_URL in the environment by load_config).
    database_url: str | None = None
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    request_timeout_seconds: float = 30.0
    use_playwright: bool = False
    # Subito only: added to every search URL as `ps` / `pe` (EUR); overrides same params in the search `url`.
    subito_price_min_eur: int = Field(default=100, ge=0)
    subito_price_max_eur: int = Field(default=2000, ge=0)
    # Kleinanzeigen: path segment ``preis:min:max`` on every SERP fetch (EUR).
    kleinanzeigen_price_min_eur: int = Field(default=150, ge=0)
    kleinanzeigen_price_max_eur: int = Field(default=1200, ge=0)
    # willhaben.at: query ``PRICE_FROM`` / ``PRICE_TO`` on every SERP fetch (EUR).
    willhaben_price_min_eur: int = Field(default=150, ge=0)
    willhaben_price_max_eur: int = Field(default=1200, ge=0)
    # bolha.com: query ``price[min]`` / ``price[max]`` on every SERP fetch (EUR).
    bolha_price_min_eur: int = Field(default=150, ge=0)
    bolha_price_max_eur: int = Field(default=1200, ge=0)
    # njuskalo.hr: query ``price[min]`` / ``price[max]`` on every SERP fetch (EUR).
    njuskalo_price_min_eur: int = Field(default=150, ge=0)
    njuskalo_price_max_eur: int = Field(default=1200, ge=0)
    # kupujemprodajem.com: query ``priceFrom`` / ``priceTo`` + ``hasPrice=yes`` (EUR in UI).
    kupujemprodajem_price_min_eur: int = Field(default=150, ge=0)
    kupujemprodajem_price_max_eur: int = Field(default=1200, ge=0)
    # jofogas.hu: query ``min_price`` / ``max_price`` in HUF; EUR band × jofogas_huf_per_eur (default ~400).
    jofogas_price_min_eur: int = Field(default=150, ge=0)
    jofogas_price_max_eur: int = Field(default=1200, ge=0)
    jofogas_huf_per_eur: float = Field(default=400.0, gt=0.0, le=10000.0)
    # buycycle.com: path segments ``min-price`` / ``max-price`` on every SERP fetch (EUR).
    buycycle_price_min_eur: int = Field(default=150, ge=0)
    buycycle_price_max_eur: int = Field(default=1200, ge=0)
    # Facebook Marketplace: query ``minPrice`` / ``maxPrice`` on every SERP fetch (EUR).
    # Ignored for ``/marketplace/np/...`` URLs (those use the YAML ``url`` string verbatim).
    facebook_price_min_eur: int = Field(default=150, ge=0)
    facebook_price_max_eur: int = Field(default=1200, ge=0)
    # Facebook Playwright: max main scroll iterations per batch (cap on computed budget).
    facebook_scroll_rounds_cap: int = Field(default=120, ge=15, le=500)
    # Consecutive rounds with no new DOM cards and no new XHR listing ids before stopping.
    facebook_stale_rounds: int = Field(default=10, ge=1, le=100)
    # Do not stop on stale until this many scroll rounds have run (slow feeds).
    facebook_min_scroll_rounds: int = Field(default=8, ge=0, le=300)
    # eBay Browse API defaults (per-search overrides on SearchEntry).
    ebay_price_min_eur: int = Field(default=150, ge=0)
    ebay_price_max_eur: int = Field(default=1200, ge=0)
    ebay_price_min_chf: int = Field(default=140, ge=0)
    ebay_price_max_chf: int = Field(default=1130, ge=0)
    # OR = comma inside one pair of parens (Browse API). Avoid bare "bike" — it matches motorcycles.
    # Set to "" for keywordless eBay search (category_ids / epid / gtin only) when searches omit ebay_query.
    ebay_default_query: str = "(bicycle, bicicletta, bici, fahrrad)"
    ebay_default_marketplace_ids: list[str] = Field(
        default_factory=lambda: ["EBAY_IT", "EBAY_AT", "EBAY_CH"]
    )
    # Browse API category_ids: limits to complete bicycles on IT/AT/CH (Biciclette / Fahrräder).
    # Set to "" to disable. Per-search `ebay_category_ids` overrides. IDs can differ on other sites.
    ebay_default_category_ids: str = "177831"

    @model_validator(mode="after")
    def _subito_price_range(self) -> AppConfig:
        if self.subito_price_min_eur > self.subito_price_max_eur:
            raise ValueError("subito_price_min_eur must be <= subito_price_max_eur")
        return self

    @model_validator(mode="after")
    def _kleinanzeigen_price_range(self) -> AppConfig:
        if self.kleinanzeigen_price_min_eur > self.kleinanzeigen_price_max_eur:
            raise ValueError(
                "kleinanzeigen_price_min_eur must be <= kleinanzeigen_price_max_eur"
            )
        return self

    @model_validator(mode="after")
    def _willhaben_price_range(self) -> AppConfig:
        if self.willhaben_price_min_eur > self.willhaben_price_max_eur:
            raise ValueError(
                "willhaben_price_min_eur must be <= willhaben_price_max_eur"
            )
        return self

    @model_validator(mode="after")
    def _bolha_price_range(self) -> AppConfig:
        if self.bolha_price_min_eur > self.bolha_price_max_eur:
            raise ValueError(
                "bolha_price_min_eur must be <= bolha_price_max_eur"
            )
        return self

    @model_validator(mode="after")
    def _njuskalo_price_range(self) -> AppConfig:
        if self.njuskalo_price_min_eur > self.njuskalo_price_max_eur:
            raise ValueError(
                "njuskalo_price_min_eur must be <= njuskalo_price_max_eur"
            )
        return self

    @model_validator(mode="after")
    def _kupujemprodajem_price_range(self) -> AppConfig:
        if self.kupujemprodajem_price_min_eur > self.kupujemprodajem_price_max_eur:
            raise ValueError(
                "kupujemprodajem_price_min_eur must be <= kupujemprodajem_price_max_eur"
            )
        return self

    @model_validator(mode="after")
    def _jofogas_price_range(self) -> AppConfig:
        if self.jofogas_price_min_eur > self.jofogas_price_max_eur:
            raise ValueError(
                "jofogas_price_min_eur must be <= jofogas_price_max_eur"
            )
        return self

    @model_validator(mode="after")
    def _buycycle_price_range(self) -> AppConfig:
        if self.buycycle_price_min_eur > self.buycycle_price_max_eur:
            raise ValueError(
                "buycycle_price_min_eur must be <= buycycle_price_max_eur"
            )
        return self

    @model_validator(mode="after")
    def _facebook_price_range(self) -> AppConfig:
        if self.facebook_price_min_eur > self.facebook_price_max_eur:
            raise ValueError(
                "facebook_price_min_eur must be <= facebook_price_max_eur"
            )
        return self

    @model_validator(mode="after")
    def _facebook_scroll_settings(self) -> AppConfig:
        if self.facebook_min_scroll_rounds > self.facebook_scroll_rounds_cap:
            raise ValueError(
                "facebook_min_scroll_rounds must be <= facebook_scroll_rounds_cap"
            )
        return self

    @model_validator(mode="after")
    def _ebay_price_ranges(self) -> AppConfig:
        if self.ebay_price_min_eur > self.ebay_price_max_eur:
            raise ValueError("ebay_price_min_eur must be <= ebay_price_max_eur")
        if self.ebay_price_min_chf > self.ebay_price_max_chf:
            raise ValueError("ebay_price_min_chf must be <= ebay_price_max_chf")
        return self

    @model_validator(mode="after")
    def _require_database_url(self) -> AppConfig:
        if not (self.database_url or "").strip():
            raise ValueError(
                "database_url is required: set DATABASE_URL in .env and/or database_url in config YAML"
            )
        return self


def load_config(path: Path) -> AppConfig:
    load_dotenv()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    env_db = (os.environ.get("DATABASE_URL") or "").strip()
    if env_db:
        raw["database_url"] = env_db
    env_vertex_project_id = (os.environ.get("VERTEX_PROJECT_ID") or "").strip()
    env_vertex_location = (os.environ.get("VERTEX_LOCATION") or "").strip()
    env_vertex_gcs_bucket = (os.environ.get("VERTEX_GCS_BUCKET") or "").strip()
    if env_vertex_project_id:
        raw["vertex_project_id"] = env_vertex_project_id
    if env_vertex_location:
        raw["vertex_location"] = env_vertex_location
    if env_vertex_gcs_bucket:
        raw["vertex_gcs_bucket"] = env_vertex_gcs_bucket
    return AppConfig.model_validate(raw)
