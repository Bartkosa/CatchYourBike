from __future__ import annotations

import os

import httpx


def send_telegram_message(
    text: str,
    *,
    dry_run: bool = False,
    parse_mode: str | None = None,
    bot_token: str | None = None,
    chat_id: str | None = None,
    dry_run_label: str | None = None,
) -> bool:
    token = (bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")).strip()
    cid = (chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")).strip()
    if dry_run:
        label = (dry_run_label or "telegram").strip()
        print(f"[{label}]\n{text}", flush=True)
        return True
    if not token or not cid:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in environment")

    body = text[:3500]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload: dict = {
        "chat_id": cid,
        "text": body,
        "disable_web_page_preview": False,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    r = httpx.post(url, json=payload, timeout=30.0)
    r.raise_for_status()
    data = r.json()
    return bool(data.get("ok"))
