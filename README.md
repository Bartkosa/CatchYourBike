# Bikefinder — multi-source stolen bike monitor

Personal tool: runs configured search URLs (marketplaces are **pluggable**), compares listing photos against your reference photos with **Gemini Vision**, stores history in **PostgreSQL**, and sends **Telegram** alerts. 

## Ethics

- Use **slow**, **low-volume** checks (defaults are conservative).
- This does **not** replace a police report or Subito’s abuse/stolen-goods reporting.

## Setup

```bash
cd krossScraper
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

After pulling changes that add Vertex/GCS support, run `pip install -e .` again in the same venv so `google-genai` and `google-cloud-storage` are installed (otherwise you may see `ModuleNotFoundError: No module named 'google'`).

1. Create a PostgreSQL database and set **`DATABASE_URL`** in `.env` (e.g. `postgresql://user:pass@host:5432/dbname`). You can optionally set **`database_url`** in `config.yaml`; if both are set, **`DATABASE_URL` wins**.
2. Copy `config/config.example.yaml` → `config/config.yaml` and set `searches` (each entry may set `source`, default `subito`) and `reference_images`. For **Subito**, paste your search URL (sort, region, etc.); pagination rewrites `o` and **`subito_price_min_eur` / `subito_price_max_eur`** set `ps` / `pe` on every request.
3. Copy `.env.example` → `.env` and set `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `DATABASE_URL`, `VERTEX_PROJECT_ID`, `VERTEX_LOCATION`, and `VERTEX_GCS_BUCKET`.
   - Authenticate ADC once: `gcloud auth application-default login` (Gemini calls now use Vertex AI + ADC, not API key).
   - Create a bot with [@BotFather](https://t.me/BotFather), copy the token.
   - Chat ID: message [@userinfobot](https://t.me/userinfobot) or your bot and open `https://api.telegram.org/bot<TOKEN>/getUpdates` to read `chat.id`.
   - **Optional second bot:** set `TELEGRAM_HIGH_CONFIDENCE_BOT_TOKEN` and `TELEGRAM_HIGH_CONFIDENCE_CHAT_ID` (another bot from BotFather; can use the same chat ID). That channel receives “high confidence” matches when Gemini score is **strictly greater** than `high_confidence_score_gt` (default `0.90`), **or** when the listing title contains `high_confidence_title_substring` (default `kross`, case-insensitive) **and** score is **strictly greater** than `high_confidence_title_min_score` (default `0.6`). The main bot still receives every alert with score ≥ `gemini_threshold`. Tune these in `config.yaml`.

## Run

```bash
bikefinder run
bikefinder run --dry-run
bikefinder run --config path/to/config.yaml
bikefinder crawl
bikefinder score --dry-run
bikefinder run --only-source subito
bikefinder run --only-source wallapop
bikefinder run --only-source kupujemprodajem
bikefinder run --only-source facebook
bikefinder run --only-search italy_bikes_recent
bikefinder run --only-source subito,wallapop
bikefinder run --watch --interval-minutes 30
bikefinder run --backfill
bikefinder run --backfill --dry-run
bikefinder crawl --today
```

### Windows Task Scheduler (hourly)

Use **two tasks** with the same `config.yaml`:

1. `bikefinder crawl --today` — ingest listings for today’s UTC calendar day (and backfill to that floor).
2. `bikefinder score` — Gemini only on rows still marked **needs scoring** (`image_relevance = -1` with non-empty `image_urls`), optionally narrowed by `--only-source` / `--only-search` like crawl. Listing age is **not** filtered here; `min_listing_date` applies to **crawl** only.

Add `--only-source subito` (or `wallapop`, `ebay`) on each command if you split work per marketplace.

### Two-Terminal Workflow

Use this when you want to fully crawl the DB first, then run Gemini scoring separately. **Same commands for every marketplace** (Subito, Wallapop, …).

- Terminal A (crawl + fill DB): `bikefinder crawl --only-source subito` or `bikefinder crawl --only-source wallapop` (optional `--only-search <exact YAML name>`)
- Terminal B (Gemini scoring): `bikefinder score --only-source subito` or `bikefinder score --only-source wallapop` (add `--dry-run` to avoid Telegram)

Wallapop often needs `use_playwright: true` in `config.yaml` for fetching; `crawl` and `score` work the same as Subito once listings are in Postgres.

The crawler marks listings as "needs Gemini" via `image_relevance = -1.0` and stores `image_urls` in Postgres; `score` only pulls rows with **`image_relevance = -1`** (and images), whose `listing_id` prefix and `search_name` match the searches left in config after `--only-source` / `--only-search` filters.

### Crawl Modes

- Newest-only crawl (default): stop when the crawler hits the first listing that already exists in the DB (or when the SERP ends). Listings at/before the `min_listing_date` floor are **skipped** (not upserted) but **do not** stop paging—only an existing DB row does.
- One-shot crawl for today (sets `min_listing_date` to today's UTC date and enables backfill): `bikefinder crawl --today`.
- Full backfill crawl: `bikefinder crawl --only-source subito --backfill` (continues paging until the first listing on/before `min_listing_date`, or SERP end). Same as setting `backfill: true` in YAML (CLI flag ORs with that key, like `bikefinder run`).

Notes:
- Listings with `posted_at >= min_listing_date` (UTC, `YYYY-MM-DD` or `DD.MM.YYYY`) are crawled into Postgres.
- In the combined `bikefinder run` mode, Gemini is executed and `image_relevance` / `gemini_reason` are filled for new listings.
- In the split workflow, `bikefinder crawl` inserts rows with `image_relevance=-1.0` and `bikefinder score` fills Gemini scoring for those rows.
- Wallapop Playwright opens your **exact** search URL (no ``page=`` / no ``#`` on the real SERP). It collects listing JSON from **/search/section** and **/general/search** responses, clicks **Carica altro** (with retries) and scrolls as fallback, and if the UI stops returning new JSON it calls **/api/v3/general/search** again via the same browser cookie jar (``next_page`` token or ``page=2``, …). Up to ``max_pages_per_search`` chunks per batch are **merged and sorted globally newest-first** before crawl frontiers run. Logs use ``chunk=0``, ``chunk=1``, …
- One-shot stop frontiers are based on the *first* listings encountered while paging newest-first (within each fetched page, items are sorted newest-first so this matches Subito-style SERP order):
  - `bikefinder crawl` (newest-only): stop at the first listing already in the DB, or SERP end (see Crawl Modes above). **`bikefinder crawl --backfill`** (or `backfill: true`): stop at the date floor, or SERP end.
  - Default `bikefinder run` (newest-only): stop at the first listing that already exists in the DB; if none are found, stop when the date frontier is reached (`posted_at < min_listing_date`).
  - `bikefinder run --backfill` (fresh-fill): ignore DB presence and keep crawling until the date frontier is reached (`posted_at < min_listing_date`).
- Rows with `image_relevance = -1` are "needs Gemini"; `bikefinder score` loads only those (with images). `image_relevance = 0` means no images / do not score.
- `--only-source` limits runs to searches whose YAML `source` matches (case-insensitive). Repeat the flag or pass a comma-separated list so **one shared `config.yaml`** can back **several scheduled tasks**—one per marketplace.
- `--only-search` limits runs to searches whose YAML `name` matches **exactly** (repeat or comma-separated). If both filters are set, a search must satisfy both.

### Backfill (fill the DB for one search window)

Use `bikefinder run` (default) for newest-only checks, or `bikefinder run --backfill` for a fresh-fill drain of the newest SERP window until the date frontier:

1. Set `min_listing_date` to the floor you want (e.g. `18.03.2026` → everything **after** that calendar day start in UTC).
2. Optionally raise `max_pages_per_search` (e.g. `10`–`30`) so each HTTP round-trip covers more pages; stay polite to the site.
3. Run **without** `--watch`: `bikefinder run --backfill` (or set `backfill: true` in YAML). Use `--dry-run` if you only want DB + logs and no Telegram sends for qualifying matches.
4. Do **not** combine `--backfill` with `--watch`.

## Windows Task Scheduler

**Single task (all searches in config)**

- Program: `C:\path\to\krossScraper\.venv\Scripts\python.exe`
- Arguments: `-m bikefinder.cli run` (or `bikefinder run` if `Scripts` is on `PATH` and the venv is activated in the task)
- Start in: `C:\path\to\krossScraper`
- Trigger: every 1–2 hours (not minutes).

Ensure `.env` and `config/config.yaml` exist in the working directory (paths are relative to **Start in**).

**Multiple tasks (one per marketplace)**

Use the same Program and Start in; create **one scheduled task per site** with different Arguments, for example:

- Task A: `-m bikefinder.cli run --only-source subito`
- Task B: `-m bikefinder.cli run --only-source wallapop`
- Task C: `-m bikefinder.cli run --only-source buycycle`

Listing IDs are prefixed with the source id (`subito:…`, etc.), so all tasks can share one database, or you can point different configs at different DBs via `--config` if you prefer isolation. If several tasks hit Postgres at once, staggering triggers by 1–5 minutes is still kind to the server.

**Alternative:** separate YAML files (e.g. `config/subito.yaml`, `config/other.yaml`) with `bikefinder run --config …` when you need different `reference_images`, thresholds, or DB URLs per site.

### Telegram bot (long-poll) in background

For the long-poll Telegram bot, this repo includes helper scripts (so you can run/stop it without a visible console window):

- Start: `start-telegram-bot.bat`
- Status: `status-telegram-bot.bat` (prints `RUNNING (PID=...)` or `STOPPED ...`)
- Stop: `stop-telegram-bot.bat`

Run them from the project directory (`krossScraper`), because the bot loads `.env` relative to its working directory.

## How matching works

- Matching is **Gemini only** (no perceptual hash / pHash, no keyword scoring).
- For each Subito results page, the scraper merges the main result `list` with promoted `galleryList` ads (often 3 extra IDs per page that are not in `list`), then batches up to 30 listings per Gemini prompt. Each listing sends up to `max_listing_images_for_gemini` photos from `__NEXT_DATA__` (not only the first).
- Images are uploaded to **Google Cloud Storage** and sent as `gs://` URIs in the prompt when possible; if upload fails, the code falls back to inline Base64.
- Gemini returns `relevance_score` (`0..1`) plus a short `reason` per candidate; batch mode uses **images only** (no titles, prices, or URLs in the prompt). The **main** Telegram bot alerts when score >= `gemini_threshold` (message includes the reason when present). If `TELEGRAM_HIGH_CONFIDENCE_*` is set, a **second** bot also gets listings where score > `high_confidence_score_gt`, or where the title matches `high_confidence_title_substring` (case-insensitive) and score > `high_confidence_title_min_score`; deduplication matches the main bot (new listing or relevance improved by more than 0.08).
- Default model is `gemini-2.5-pro` for highest comparison quality.

## Fetching Subito.it

Search requests include **`ps`** / **`pe`** from **`subito_price_min_eur`** and **`subito_price_max_eur`** in `config.yaml` (defaults 100–2000 EUR).

Subito often returns **403** to requests that look like generic Python clients. This project uses [**curl_cffi**](https://github.com/lexiforest/curl_cffi) with a Chrome TLS fingerprint, then reads listing data from the `__NEXT_DATA__` JSON blob (same idea as the in-browser view).

## Playwright fallback

If plain HTTP returns empty data (rare), install browsers and enable in YAML:

```bash
pip install -e ".[playwright]"
playwright install chromium
```

Set `use_playwright: true` in `config.yaml`.

## Adding another marketplace (e.g. Kleinanzeigen, Wallapop)

1. Implement `ListingSource` in `src/bikefinder/sources/base.py`: `fetch_search_pages`, `parse_posted_at`, and stable `source_id` / `display_name`.
2. Normalize every listing to `listing_id = "{source_id}:{native_id}"` and set `Listing.source` so DB keys never collide between sites.
3. Register the instance in `src/bikefinder/sources/__init__.py` (`LISTING_SOURCES` or `register_listing_source(...)`).
4. Add a YAML search with `source: your_id` and the site’s search URL.

### Wallapop support (v1)

- Source id: `wallapop`
- Search URL must be `https://it.wallapop.com/search` with `category_id=17000`, `order_by=newest`, and your chosen `min_sale_price` / `max_sale_price` (extra query params like `utm_*` are OK).
- Raise **`max_pages_per_search`** to crawl more SERPs; with Playwright, each page is `?page=1`, `?page=2`, … (~40 listings per page).
- Listing photos come from API fields `images[].urls.big` (etc.); if older rows have `image_urls = []`, run **`bikefinder crawl --only-source wallapop` again** so Postgres picks up URLs.
- Example: `bikefinder crawl --only-source wallapop` then `bikefinder score --only-source wallapop`

Low-level Subito HTTP/HTML parsing lives under `src/bikefinder/sources/subito/` (`client.py`, `serp_html.py`); new sites get a package with client/parser modules and a thin `*Source` class.

### buycycle.com

- Source id: `buycycle`
- Install browsers: `pip install -e ".[playwright]"` then `playwright install chromium` (the SERP is fetched only via Playwright: Cookiebot + scroll, then Constructor.io product cards).
- Search URL: `https://buycycle.com/<locale>/shop/...` including `/shop/` and your filters; use a path that ends with `sort-by/new` for newest-first. YAML keys `buycycle_price_min_eur` / `buycycle_price_max_eur` insert `min-price` / `max-price` path segments before `sort-by` on every request; pagination appends `/page/N` (e.g. `.../sort-by/new/page/3`).
- Listing timestamps are not exposed on the grid; the scraper assigns a synthetic monotonic `posted_at` (newer rows on earlier pages get later UTC times) so crawl cutoffs and logging behave like other sources.

### Facebook Marketplace

- Source id: `facebook`
- **Playwright is required** (same install as buycycle). There is no reliable unauthenticated HTTP API for Marketplace in this project.
- In Facebook’s UI, set your search (e.g. bicycles / “bici”), **sort by newest** (“Date listed: Newest first”), **price** and **location + radius** (e.g. Padua and 500 km), then copy the resulting `https://www.facebook.com/marketplace/...` URL into YAML `url`. Location-based searches often look like `/marketplace/np/<locationId>/search?query=...&radius=500&...` — for these **`np`** URLs the scraper uses your string **exactly** (no re-encoding, no injection of `minPrice` / `maxPrice` / `sortBy` from YAML): Meta often returns `ERR_TOO_MANY_REDIRECTS` if the query is altered. Put `minPrice=150` and `maxPrice=1200` (or whatever you need) **inside the pasted URL**. For generic `/marketplace/search` URLs (not under `np/`), root keys `facebook_price_min_eur` / `facebook_price_max_eur` are still merged as `minPrice` / `maxPrice`, and default `sortBy=creation_time_descend` is added when missing.
- **`ERR_TOO_MANY_REDIRECTS` on `/marketplace/np/...`:** Remove `partner_ids[]`, `partner_selected`, and `hide_organic_listings` from the pasted URL (they often loop with Playwright). The fetcher also tries a stripped query automatically if the first navigation fails.
- **Query params that cap how many listings exist:** Parameters such as **`daysSinceListed`** (e.g. `daysSinceListed=1`) restrict the search to very recent listings only. That can make the SERP look “empty” or tiny compared to the same search in the browser without that filter. Remove or widen them if you want a deeper result set (independent of scroll depth).
- **Scroll tuning (optional YAML):** `facebook_scroll_rounds_cap` (max scroll iterations per batch, default 120), `facebook_stale_rounds` (consecutive rounds with no new DOM or XHR ids before stopping, default 10), `facebook_min_scroll_rounds` (minimum scroll rounds before stale-based stop is allowed, default 8). The fetcher scrolls the document and likely inner feed containers, uses **End** / **PageDown** periodically, and treats **GraphQL/XHR** listing-id growth like DOM growth so it does not quit while responses are still arriving.
- **Debugging empty SERP:** Set **`FACEBOOK_TRACE_RESPONSES=1`** in the environment to log marketplace-related XHR/fetch URLs and how many listing ids were parsed from each response (helps confirm whether you are logged in and whether Meta returns feed JSON).
- **Session (required in practice):** Headless Chromium with no cookies often hits **`ERR_TOO_MANY_REDIRECTS`**, a login wall, or an empty grid. Use one of:
  1. **`FACEBOOK_BROWSER_USER_DATA_DIR`** — path to an empty folder; Playwright keeps a real profile there. One-time login: `python scripts/facebook_open_profile.py` with that env var set (opens a window; log in, then press Enter). Then run `bikefinder crawl --only-source facebook` with the same variable (headless is fine after login).
  2. **`FACEBOOK_STORAGE_STATE_PATH`** — `storage_state.json` from a logged-in session (keep private; do not commit).
  3. If it still fails: **`FACEBOOK_USE_SYSTEM_CHROME=1`** (use installed Chrome) and/or **`FACEBOOK_HEADFUL=1`** (visible browser so you can pass checkpoints). See `.env.example`.
- The scraper scrolls the feed, scans **all** `a[href]` (and elements with `href` containing `marketplace/item`) for listing links, and also **regex-scans GraphQL** (`/api/graphql`) response bodies for `marketplace/item/<id>` (many SERPs render mostly from XHR). If no time is found, it falls back to **synthetic** `posted_at` values (similar tradeoff to buycycle) so `min_listing_date` and crawl logging still run.
- Expect **breakage** when Meta changes the page or API shapes; stay low-volume and be aware of Meta’s terms for automated access.

## Project layout

- `src/bikefinder/` — CLI, Gemini compare, PostgreSQL storage, Telegram
- `src/bikefinder/sources/` — marketplace packages (`subito/`, `wallapop/`, `ebay/`, `facebook/`, …)
- `config/config.yaml` — your config (gitignored if you prefer; example is `config.example.yaml`)
- Postgres table `listings` — created on first run (legacy bare numeric Subito IDs are migrated to `subito:<id>` on connect; `image_urls` are persisted for Gemini; `image_relevance` sentinel `-1.0` means “needs scoring”)

## Crawler crawl logs (`crawl_page_logs`)

On each run, the crawler inserts one row per fetched paginated search-results URL into `crawl_page_logs`, including:

- `source` (e.g. `subito`), `search_name`
- `page_url` (full URL including pagination and `ps`/`pe`)
- `crawled_at` (UTC)
- `already_in_db_count`, `added_to_db_count`, `before_cutoff_count`
- `min_listing_posted_at`, `max_listing_posted_at` (UTC, parsed from listing `posted_at`)

Example queries:

- Latest page fetches:

```sql
SELECT
  crawled_at, source, search_name, page_url,
  already_in_db_count, added_to_db_count, before_cutoff_count,
  min_listing_posted_at, max_listing_posted_at
FROM crawl_page_logs
ORDER BY crawled_at DESC
LIMIT 50;
```

- Totals per source/search (last 24h):

```sql
SELECT
  source,
  search_name,
  SUM(already_in_db_count) AS already_in_db,
  SUM(added_to_db_count) AS added_to_db,
  SUM(before_cutoff_count) AS before_cutoff
FROM crawl_page_logs
WHERE crawled_at >= NOW() - INTERVAL '24 hours'
GROUP BY source, search_name
ORDER BY source, search_name;
```
