"""
One-time helper: open Facebook in Playwright with a persistent profile so cookies survive.

Usage (PowerShell):
  $env:FACEBOOK_BROWSER_USER_DATA_DIR = "C:\\Users\\YOU\\fb_playwright_profile"
  .\\.venv\\Scripts\\python.exe scripts\\facebook_open_profile.py

Log in, open Marketplace and your search if you like, then press Enter in this terminal.
After that, run bikefinder with the same FACEBOOK_BROWSER_USER_DATA_DIR (headless is OK).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    profile = (os.environ.get("FACEBOOK_BROWSER_USER_DATA_DIR") or "").strip()
    if not profile:
        print(
            "Set environment variable FACEBOOK_BROWSER_USER_DATA_DIR to a folder path "
            "(will be created). Example:\n"
            '  $env:FACEBOOK_BROWSER_USER_DATA_DIR = "C:\\\\Users\\\\YOU\\\\fb_playwright_profile"',
            file=sys.stderr,
        )
        return 1
    Path(profile).mkdir(parents=True, exist_ok=True)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print('Install Playwright: pip install ".[playwright]" && playwright install chrome', file=sys.stderr)
        return 1

    use_chrome = (os.environ.get("FACEBOOK_USE_SYSTEM_CHROME") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    channel = "chrome" if use_chrome else None
    print(f"Profile: {profile}\nChannel: {channel or 'Chromium bundle'}\n", flush=True)

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            profile,
            headless=False,
            channel=channel,
            viewport={"width": 1400, "height": 900},
            locale="it-IT",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
            ignore_default_args=["--enable-automation"],
        )
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=120_000)
            input(
                "\nLog into Facebook, open Marketplace if needed, then press Enter here to save and exit…\n"
            )
        finally:
            ctx.close()
    print("Profile saved. Use the same FACEBOOK_BROWSER_USER_DATA_DIR when running bikefinder.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
