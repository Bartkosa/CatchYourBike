from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass

# Subcommands that hit marketplace SERPs (scraping).
_SCRAPE_SUBCOMMANDS = frozenset({"crawl", "run"})


@dataclass(frozen=True)
class BikefinderProcessRow:
    pids: tuple[int, ...]
    """One logical CLI invocation may appear as several OS processes (Windows launchers)."""
    subcommand: str
    """CLI subcommand if parsed (crawl, run, score, …), else ``?``."""
    cmdline_short: str
    """Truncated command line for display (best representative of the group)."""
    is_scrape: bool
    """True when subcommand is ``crawl`` or ``run`` (SERP ingestion)."""


def _join_cmdline(parts: list[str], *, max_parts: int = 12, max_len: int = 220) -> str:
    tail = " …" if len(parts) > max_parts else ""
    chunk = " ".join(parts[:max_parts])
    if len(chunk) > max_len:
        return chunk[: max_len - 1] + "…"
    return chunk + tail


def _cmdline_mentions_bikefinder(parts: list[str]) -> bool:
    blob = " ".join(parts).lower()
    if "bikefinder.cli" in blob:
        return True
    return any(str(p).lower().endswith("bikefinder.exe") for p in parts)


def parse_bikefinder_subcommand_and_argv_tail(
    parts: list[str],
) -> tuple[str, tuple[str, ...]] | None:
    """Subcommand plus argv tokens after it (used to merge duplicate Windows process chains)."""
    pl = [str(p) for p in parts]
    low = [x.lower().replace("\\", "/") for x in pl]

    for i in range(len(low)):
        if low[i] == "-m" and i + 1 < len(low):
            mod = low[i + 1]
            if mod.endswith("bikefinder.cli") or mod.rstrip("/").endswith("/bikefinder.cli"):
                if i + 2 < len(low):
                    idx = i + 2
                    sub = pl[idx].lower()
                    tail = tuple(pl[idx + 1 :])
                    return sub, tail
        exe = low[i]
        if exe.endswith("/bikefinder.exe") or exe.endswith("bikefinder.exe"):
            if i + 1 < len(low):
                idx = i + 1
                sub = pl[idx].lower()
                tail = tuple(pl[idx + 1 :])
                return sub, tail
        if exe.endswith("cli.py") and "bikefinder" in exe and i + 1 < len(low):
            idx = i + 1
            sub = pl[idx].lower()
            tail = tuple(pl[idx + 1 :])
            return sub, tail
    return None


def parse_bikefinder_subcommand(parts: list[str]) -> str | None:
    """Return the bikefinder CLI subcommand word, or None."""
    got = parse_bikefinder_subcommand_and_argv_tail(parts)
    return got[0] if got else None


def _pick_representative_cmdline(part_lists: list[list[str]]) -> str:
    """Prefer a line that shows ``-m bikefinder.cli``; else the longest (often most informative)."""
    joined = [(pl, _join_cmdline(pl)) for pl in part_lists]
    for pl, s in joined:
        blob = " ".join(pl).lower()
        if "bikefinder.cli" in blob:
            return s
    return max((s for _, s in joined), key=len)


def list_bikefinder_processes(*, own_pid: int | None = None) -> list[BikefinderProcessRow]:
    """
    Scan local processes for bikefinder CLI invocations.

    Covers manual terminals and Task Scheduler (child processes look the same to the OS).
    Processes that cannot be read (permissions) are skipped.
    """
    try:
        import psutil
    except ImportError as e:
        raise RuntimeError(
            "Missing dependency 'psutil' (needed for /jobs). "
            "Install into the same Python you use for bikefinder, e.g.: pip install psutil"
        ) from e

    raw: list[tuple[int, str, tuple[str, ...], list[str], bool]] = []
    skip_pid = own_pid if own_pid is not None else os.getpid()

    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            pid = int(p.info["pid"])
            if pid == skip_pid:
                continue
            cmd = p.info.get("cmdline")
            if not cmd:
                continue
            parts = [str(x) for x in cmd]
            if not _cmdline_mentions_bikefinder(parts):
                continue
            parsed = parse_bikefinder_subcommand_and_argv_tail(parts)
            if parsed is None:
                sub = "?"
                tail: tuple[str, ...] = ()
            else:
                sub, tail = parsed
            scrape = sub in _SCRAPE_SUBCOMMANDS
            raw.append((pid, sub, tail, parts, scrape))
        except (psutil.NoSuchProcess, psutil.AccessDenied, TypeError, ValueError):
            continue

    groups = defaultdict(list)
    for pid, sub, tail, parts, scrape in raw:
        groups[(sub, tail)].append((pid, parts, scrape))

    out: list[BikefinderProcessRow] = []
    for (sub, _tail), items in groups.items():
        pids = tuple(sorted({i[0] for i in items}))
        scrape = items[0][2]
        part_lists = [i[1] for i in items]
        out.append(
            BikefinderProcessRow(
                pids=pids,
                subcommand=sub,
                cmdline_short=_pick_representative_cmdline(part_lists),
                is_scrape=scrape,
            )
        )

    out.sort(key=lambda r: (not r.is_scrape, r.subcommand, r.pids[0] if r.pids else 0))
    return out


def _append_job_rows(lines: list[str], block_rows: list[BikefinderProcessRow]) -> None:
    for r in block_rows:
        if len(r.pids) == 1:
            lines.append(f"  pid {r.pids[0]}  {r.subcommand}")
        else:
            plist = ", ".join(str(x) for x in r.pids)
            lines.append(
                f"  pids {plist}  {r.subcommand}  ({len(r.pids)} OS processes)"
            )
        lines.append(f"    {r.cmdline_short}")


def format_jobs_report_plain(rows: list[BikefinderProcessRow], *, generated_label: str) -> str:
    """Plain text for Telegram (no parse_mode); safe for arbitrary command lines."""
    lines = [
        "Bikefinder on this machine",
        generated_label,
        "",
    ]
    if not rows:
        lines.append("No other bikefinder CLI processes (this bot excluded).")
        lines.append("No crawling or scoring.")
        return "\n".join(lines)

    crawl_rows = [r for r in rows if r.subcommand == "crawl"]
    run_rows = [r for r in rows if r.subcommand == "run"]
    score_rows = [r for r in rows if r.subcommand == "score"]
    other = [r for r in rows if r.subcommand not in ("crawl", "run", "score")]

    if crawl_rows:
        lines.append(f"Crawling active ({len(crawl_rows)}):")
        _append_job_rows(lines, crawl_rows)
    if run_rows:
        if crawl_rows:
            lines.append("")
        lines.append(f"Run active ({len(run_rows)}):")
        _append_job_rows(lines, run_rows)
    if score_rows:
        if crawl_rows or run_rows:
            lines.append("")
        lines.append(f"Scoring active ({len(score_rows)}):")
        _append_job_rows(lines, score_rows)

    if other:
        lines.append("")
        lines.append(f"Other bikefinder ({len(other)}):")
        _append_job_rows(lines, other)

    return "\n".join(lines)
