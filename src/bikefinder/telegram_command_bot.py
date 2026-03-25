from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import httpx

from bikefinder.config import AppConfig
from bikefinder.running_probe import format_jobs_report_plain, list_bikefinder_processes
from bikefinder.storage import SourceListingStats, Storage


def _telegram_api_base(token: str) -> str:
    return f"https://api.telegram.org/bot{token.strip()}"


def _html_escape(s: str) -> str:
    """Escape for Telegram HTML (text inside ``<pre>``)."""
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _parse_command(text: str) -> tuple[str, list[str]]:
    if not text:
        return "", []
    parts = text.strip().split()
    if not parts:
        return "", []
    cmd = parts[0].split("@", 1)[0].lower()
    return cmd, parts[1:]


def _stats_table_ascii_lines(rows: list[SourceListingStats]) -> list[str]:
    """Fixed-width rows: header, rule, data, rule, ALL totals."""
    tot_scraped = sum(r.scraped_last_window for r in rows)
    tot_pend_win = sum(r.pending_among_last_window for r in rows)
    tot_db = sum(r.total_in_db for r in rows)
    tot_q = sum(r.pending_gemini_queue for r in rows)

    data_rows: list[tuple[str, int, int, int, int]] = [
        (
            r.source_id,
            r.scraped_last_window,
            r.pending_among_last_window,
            r.total_in_db,
            r.pending_gemini_queue,
        )
        for r in rows
    ]
    all_row: tuple[str, int, int, int, int] = (
        "ALL",
        tot_scraped,
        tot_pend_win,
        tot_db,
        tot_q,
    )
    width_rows = data_rows + [all_row]

    w_src = max(len("source"), max(len(t[0]) for t in width_rows))
    nums = [t[1:] for t in width_rows]
    hdr_nums = ("new", "new_queue", "total", "total_queue")
    w = [
        max(len(hdr_nums[i]), max(len(str(row[i])) for row in nums))
        for i in range(4)
    ]
    hdr = (
        "source".ljust(w_src)
        + "  "
        + hdr_nums[0].rjust(w[0])
        + "  "
        + hdr_nums[1].rjust(w[1])
        + "  "
        + hdr_nums[2].rjust(w[2])
        + "  "
        + hdr_nums[3].rjust(w[3])
    )
    rule = (
        "-" * w_src
        + "  "
        + "-" * w[0]
        + "  "
        + "-" * w[1]
        + "  "
        + "-" * w[2]
        + "  "
        + "-" * w[3]
    )

    def fmt(t: tuple[str, int, int, int, int]) -> str:
        src, new, queue_win, total_db, total_queue = t
        return (
            src.ljust(w_src)
            + "  "
            + str(new).rjust(w[0])
            + "  "
            + str(queue_win).rjust(w[1])
            + "  "
            + str(total_db).rjust(w[2])
            + "  "
            + str(total_queue).rjust(w[3])
        )

    out = [hdr, rule]
    for t in data_rows:
        out.append(fmt(t))
    out.append(rule)
    out.append(fmt(all_row))
    return out


def _format_stats_html_chunks(
    rows: list[SourceListingStats],
    *,
    hours: float,
    generated_at_utc: datetime,
    max_message_len: int = 3900,
) -> list[str]:
    """
    Telegram HTML messages: intro + monospace table in ``<pre>``.
    Splits into multiple messages if the table is very large.
    """
    window = "1 hour" if abs(hours - 1.0) < 1e-6 else f"{hours:g} hours"
    ts = generated_at_utc.replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S UTC")
    intro = (
        "<b>Listing stats by source</b>\n"
        f"Window: last {window} (UTC), generated {ts}"
    )
    if not rows:
        return [intro + "\n\n<i>(no rows in listings table)</i>"]

    table_lines = _stats_table_ascii_lines(rows)

    def one_pre(lines: list[str]) -> str:
        inner = _html_escape("\n".join(lines))
        return f"<pre>{inner}</pre>"

    full = intro + "\n\n" + one_pre(table_lines)
    if len(full) <= max_message_len:
        return [full]

    # Oversized: intro + first chunk(s) of table, then continuation messages.
    parts: list[str] = []
    header = intro + "\n\n"
    budget = max_message_len - len(header) - len("<pre></pre>") - 40
    chunk: list[str] = []
    chunk_len = 0
    part_idx = 0

    def flush_chunk(*, continued: bool) -> None:
        nonlocal chunk, chunk_len, part_idx, parts
        if not chunk:
            return
        pre = one_pre(chunk)
        prefix = (
            f"<b>Listing stats (part {part_idx + 1})</b>\n\n"
            if continued
            else header
        )
        parts.append(prefix + pre)
        part_idx += 1
        chunk = []
        chunk_len = 0

    for line in table_lines:
        line_len = len(line) + 1
        if chunk_len + line_len > budget and chunk:
            flush_chunk(continued=bool(parts))
        chunk.append(line)
        chunk_len += line_len
    flush_chunk(continued=bool(parts))
    return parts if parts else [intro + "\n\n<i>(table too large; empty split)</i>"]


def _chunk_telegram_text(text: str, max_len: int = 3900) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for line in text.split("\n"):
        add = len(line) + (1 if buf else 0)
        if size + add > max_len and buf:
            chunks.append("\n".join(buf))
            buf = [line]
            size = len(line)
        else:
            if buf:
                size += 1
            buf.append(line)
            size += len(line)
    if buf:
        chunks.append("\n".join(buf))
    return chunks


def _send_message(
    client: httpx.Client,
    token: str,
    chat_id: int | str,
    text: str,
    *,
    parse_mode: str | None = None,
) -> None:
    base = _telegram_api_base(token)
    for part in _chunk_telegram_text(text):
        payload: dict = {
            "chat_id": chat_id,
            "text": part,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        r = client.post(
            f"{base}/sendMessage",
            json=payload,
            timeout=30.0,
        )
        r.raise_for_status()


def _send_html_messages(
    client: httpx.Client,
    token: str,
    chat_id: int | str,
    html_parts: list[str],
) -> None:
    for part in html_parts:
        _send_message(client, token, chat_id, part, parse_mode="HTML")


def _stats_html_parts_for_hours(cfg: AppConfig, hours: float) -> list[str]:
    storage = Storage.from_config(cfg)
    try:
        rows = storage.fetch_listing_stats_by_source(hours=hours)
        return _format_stats_html_chunks(
            rows,
            hours=hours,
            generated_at_utc=datetime.now(timezone.utc),
        )
    finally:
        storage.close()


_HELP = (
    "Commands:\n"
    "/stats — counts by source (see /help for meanings)\n"
    "/stats <hours> — same, custom window (e.g. /stats 3)\n"
    "/jobs — crawl/run processes on this PC (Scheduler + manual)\n"
    "/help — this text"
)


def run_telegram_command_bot(
    cfg: AppConfig,
    *,
    default_stats_hours: float = 1.0,
    long_poll_timeout: int = 50,
) -> int:
    """
    Long-poll Telegram and answer /stats and /jobs from the authorized chat
    (``TELEGRAM_CHAT_ID``). ``/jobs`` only sees processes on the machine running the bot.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    allowed = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set")
    if not allowed:
        raise SystemExit(
            "TELEGRAM_CHAT_ID is not set (required so only your chat can query the bot)"
        )

    try:
        allowed_id: int | str = int(allowed)
    except ValueError:
        allowed_id = allowed

    base = _telegram_api_base(token)
    offset = 0

    print(
        "[telegram-bot] listening for commands; send /stats or /jobs from your linked chat "
        f"(chat_id={allowed_id}). Ctrl+C to stop.",
        flush=True,
    )

    try:
        with httpx.Client() as client:
            while True:
                try:
                    r = client.get(
                        f"{base}/getUpdates",
                        params={"offset": offset, "timeout": long_poll_timeout},
                        timeout=float(long_poll_timeout) + 15.0,
                    )
                    r.raise_for_status()
                    data = r.json()
                except (httpx.HTTPError, ValueError) as e:
                    print(f"[telegram-bot] getUpdates error: {e}; retry in 5s", flush=True)
                    time.sleep(5.0)
                    continue

                if not data.get("ok"):
                    print(f"[telegram-bot] getUpdates not ok: {data!r}; retry in 5s", flush=True)
                    time.sleep(5.0)
                    continue

                for upd in data.get("result", []):
                    offset = int(upd["update_id"]) + 1
                    msg = upd.get("message") or upd.get("edited_message")
                    if not msg:
                        continue
                    chat = msg.get("chat") or {}
                    chat_id = chat.get("id")
                    if chat_id is None:
                        continue
                    if str(chat_id) != str(allowed_id):
                        print(f"[telegram-bot] ignored chat_id={chat_id}", flush=True)
                        continue

                    text = (msg.get("text") or "").strip()
                    cmd, args = _parse_command(text)
                    if cmd in ("/start", "/help"):
                        _send_message(client, token, chat_id, _HELP)
                        continue
                    if cmd == "/stats":
                        hours = default_stats_hours
                        if args:
                            try:
                                hours = float(args[0])
                            except ValueError:
                                _send_message(
                                    client,
                                    token,
                                    chat_id,
                                    "Usage: /stats [hours] — hours must be a number, e.g. /stats 2",
                                )
                                continue
                            if hours <= 0 or hours > 24 * 365:
                                _send_message(
                                    client,
                                    token,
                                    chat_id,
                                    "hours must be > 0 and at most 8760 (one year).",
                                )
                                continue
                        try:
                            html_parts = _stats_html_parts_for_hours(cfg, hours)
                        except Exception as e:
                            _send_message(client, token, chat_id, f"Failed to load stats: {e}")
                            continue
                        _send_html_messages(client, token, chat_id, html_parts)
                        continue

                    if cmd == "/jobs":
                        try:
                            rows = list_bikefinder_processes(own_pid=os.getpid())
                            stamp = datetime.now(timezone.utc).replace(microsecond=0)
                            label = stamp.strftime("Checked %Y-%m-%d %H:%M:%S UTC")
                            body = format_jobs_report_plain(
                                rows, generated_label=label
                            )
                        except Exception as e:
                            body = f"Could not scan processes: {e}"
                        _send_message(client, token, chat_id, body)
                        continue

                    if cmd.startswith("/"):
                        _send_message(
                            client,
                            token,
                            chat_id,
                            "Unknown command. Try /help",
                        )
    except KeyboardInterrupt:
        print("\n[telegram-bot] stopped.", flush=True)
    return 0
