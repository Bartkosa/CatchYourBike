from __future__ import annotations

from bikefinder.running_probe import (
    format_jobs_report_plain,
    parse_bikefinder_subcommand,
    parse_bikefinder_subcommand_and_argv_tail,
)


def test_parse_subcommand_module_invocation() -> None:
    cmd = [
        r"C:\proj\.venv\Scripts\python.exe",
        "-m",
        "bikefinder.cli",
        "crawl",
        "--today",
    ]
    assert parse_bikefinder_subcommand(cmd) == "crawl"


def test_parse_subcommand_console_script() -> None:
    cmd = [r"C:\proj\.venv\Scripts\bikefinder.exe", "score", "--dry-run"]
    assert parse_bikefinder_subcommand(cmd) == "score"


def test_format_jobs_report_empty() -> None:
    text = format_jobs_report_plain([], generated_label="t0")
    assert "No other bikefinder" in text
    assert "No crawling or scoring" in text


def test_format_jobs_report_scrape_and_other() -> None:
    from bikefinder.running_probe import BikefinderProcessRow

    rows = [
        BikefinderProcessRow(
            pids=(111,),
            subcommand="crawl",
            cmdline_short="python -m bikefinder.cli crawl",
            is_scrape=True,
        ),
        BikefinderProcessRow(
            pids=(222,),
            subcommand="telegram-bot",
            cmdline_short="python -m bikefinder.cli telegram-bot",
            is_scrape=False,
        ),
    ]
    text = format_jobs_report_plain(rows, generated_label="t0")
    assert "Crawling active" in text
    assert "pid 111" in text
    assert "Other bikefinder" in text
    assert "pid 222" in text
