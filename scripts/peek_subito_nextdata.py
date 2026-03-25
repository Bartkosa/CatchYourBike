"""One-off: print Subito search __NEXT_DATA__ items.* keys (pagination hints)."""
from __future__ import annotations

import json
import re
import sys

from curl_cffi import requests as cr


def _dump_sample(items: dict, key: str) -> None:
    lst = items.get(key)
    if not isinstance(lst, list) or not lst:
        return
    for i, entry in enumerate(lst[:8]):
        if not isinstance(entry, dict):
            print(key, i, type(entry))
            continue
        item = entry.get("item") if isinstance(entry.get("item"), dict) else entry
        kind = item.get("kind") if isinstance(item, dict) else None
        keys = sorted(item.keys())[:30] if isinstance(item, dict) else None
        print(key, i, "kind=", kind, "keys=", keys)


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else (
        "https://www.subito.it/annunci-italia/vendita/biciclette/?order=datedesc"
    )
    r = cr.get(url, impersonate="chrome120", timeout=60)
    r.raise_for_status()
    m = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        r.text,
        re.DOTALL,
    )
    if not m:
        print("No __NEXT_DATA__")
        sys.exit(1)
    d = json.loads(m.group(1))
    items = (
        d.get("props", {})
        .get("pageProps", {})
        .get("initialState", {})
        .get("items", {})
    )
    print("items keys:", sorted(items.keys()))
    for k in sorted(items.keys()):
        v = items[k]
        if isinstance(v, (int, float, str, bool)) or v is None:
            print(f"  {k} = {v!r}")
    for key in ("list", "rankedList", "originalList", "galleryList", "boostedItems"):
        lst = items.get(key)
        if isinstance(lst, list):
            print(f"{key} len:", len(lst))
            kinds: dict[str, int] = {}
            for entry in lst[:80]:
                if not isinstance(entry, dict):
                    kinds[type(entry).__name__] = kinds.get(type(entry).__name__, 0) + 1
                    continue
                item = entry.get("item") if isinstance(entry.get("item"), dict) else entry
                kk = str(item.get("kind") if isinstance(item, dict) else "?")
                kinds[kk] = kinds.get(kk, 0) + 1
            print(f"  kinds (first 80): {kinds}")
    print("--- rankedList samples ---")
    _dump_sample(items, "rankedList")
    rl = items.get("rankedList")
    if isinstance(rl, list):
        for i in (0, 4, 5, 10, 15, 20):
            if i >= len(rl):
                break
            e = rl[i]
            if not isinstance(e, dict):
                continue
            print("rankedList entry", i, "top keys:", sorted(e.keys()))
            it = e.get("item") if isinstance(e.get("item"), dict) else e
            if isinstance(it, dict):
                print("  item keys count:", len(it), "has subject:", "subject" in it)

    for name in ("list", "galleryList"):
        lst = items.get(name)
        if not isinstance(lst, list) or not lst:
            continue
        e0 = lst[0]
        if isinstance(e0, dict):
            it = e0.get("item") if isinstance(e0.get("item"), dict) else e0
            print(name, "entry[0] top keys:", sorted(e0.keys())[:12])
            if isinstance(it, dict):
                print(
                    "  inner kind:",
                    it.get("kind"),
                    "keys count:",
                    len(it),
                    "has subject:",
                    "subject" in it,
                )

    _ids_from_items(items)


def _ids_from_items(items: dict) -> None:
    def collect(lst_key: str) -> set[str]:
        out: set[str] = set()
        lst = items.get(lst_key)
        if not isinstance(lst, list):
            return out
        for entry in lst:
            if not isinstance(entry, dict):
                continue
            it = entry.get("item") if isinstance(entry.get("item"), dict) else entry
            if not isinstance(it, dict):
                continue
            urn = str(it.get("urn") or "")
            if ":list:" in urn:
                out.add(urn.split(":list:")[-1])
        return out

    a = collect("list")
    b = collect("galleryList")
    print("list ids count:", len(a), "gallery ids count:", len(b))
    print("gallery only (not in list):", sorted(b - a))
    print("overlap:", len(a & b))


if __name__ == "__main__":
    main()
