from __future__ import annotations

import hashlib
import imghdr
import json
import time
from pathlib import Path
from typing import MutableMapping

import httpx
import google.genai as genai
from google.cloud import storage
from google.genai import types

from bikefinder.config import AppConfig
from bikefinder.models import Listing

def _write_last_gemini_prompt(endpoint: str, payload: dict) -> None:
    project_root = Path(__file__).resolve().parents[2]
    output_path = project_root / "data" / "last_gemini_prompt.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps({"endpoint": endpoint, "payload": payload}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _mime_for_bytes(data: bytes) -> str:
    kind = imghdr.what(None, h=data)
    if kind == "png":
        return "image/png"
    if kind == "gif":
        return "image/gif"
    if kind == "webp":
        return "image/webp"
    return "image/jpeg"


def _gemini_reason_text(raw: object, *, max_len: int = 400) -> str:
    if raw is None:
        return ""
    s = str(raw).strip().replace("\n", " ")
    if len(s) > max_len:
        return s[: max_len - 1] + "…"
    return s


def _inline_part_for_bytes(data: bytes) -> types.Part:
    return types.Part.from_bytes(data=data, mime_type=_mime_for_bytes(data))


def _file_part_for_uri(mime_type: str, file_uri: str) -> types.Part:
    return types.Part.from_uri(file_uri=file_uri, mime_type=mime_type)


def _vertex_endpoint(cfg: AppConfig) -> str:
    return (
        f"vertex://{cfg.vertex_location}/{cfg.vertex_project_id}/models/"
        f"{cfg.gemini_model}:generateContent"
    )


def _vertex_client(cfg: AppConfig) -> genai.Client:
    return genai.Client(
        vertexai=True,
        project=cfg.vertex_project_id,
        location=cfg.vertex_location,
    )


def _upload_bytes_to_gcs(
    gcs_client: storage.Client,
    bucket_name: str,
    data: bytes,
    mime_type: str,
    display_name: str,
    *,
    object_prefix: str = "bikefinder-gemini",
) -> str:
    digest = hashlib.sha256(data).hexdigest()
    ext = mime_type.split("/")[-1]
    object_name = f"{object_prefix}/{display_name}-{digest[:16]}.{ext}"
    bucket = gcs_client.bucket(bucket_name)
    blob = bucket.blob(object_name)
    if not blob.exists():
        blob.upload_from_string(data, content_type=mime_type)
    return f"gs://{bucket_name}/{object_name}"


def _image_part_for_gemini(
    gcs_client: storage.Client,
    bucket_name: str,
    data: bytes,
    display_name: str,
    cache: MutableMapping[tuple[str, str], str],
    _upload_timeout: float,
    *,
    use_gcs_uri: bool,
) -> tuple[types.Part, str, float]:
    """
    Prefer GCS URI; fall back to inline base64 on failure.
    """
    t0 = time.monotonic()
    if not use_gcs_uri:
        part = _inline_part_for_bytes(data)
        return part, "inline_no_gcs", time.monotonic() - t0
    mime_type = _mime_for_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    cache_key = (digest, mime_type)
    if cache_key in cache:
        part = _file_part_for_uri(mime_type, cache[cache_key])
        return part, "cache", time.monotonic() - t0

    try:
        uri = _upload_bytes_to_gcs(
            gcs_client,
            bucket_name,
            data,
            mime_type,
            display_name,
        )
        cache[cache_key] = uri
        part = _file_part_for_uri(mime_type, uri)
        return part, "file", time.monotonic() - t0
    except (OSError, RuntimeError, TimeoutError, ValueError):
        part = _inline_part_for_bytes(data)
        return part, "inline", time.monotonic() - t0


def _load_reference_images(paths: list[str]) -> list[bytes]:
    imgs: list[bytes] = []
    for p in paths:
        path = Path(p)
        if path.is_file():
            imgs.append(path.read_bytes())
    return imgs


def _load_listing_images(
    image_urls: list[str],
    client: httpx.Client,
    user_agent: str,
    max_images: int,
) -> list[bytes]:
    out: list[bytes] = []
    headers = {"User-Agent": user_agent}
    for u in image_urls[:max_images]:
        try:
            r = client.get(u, headers=headers, follow_redirects=True, timeout=30.0)
            r.raise_for_status()
            out.append(r.content)
        except httpx.HTTPError:
            continue
    return out


def _gemini_rate_limit_wait_s(attempt: int) -> float:
    """Seconds to wait before retrying after transient/rate errors."""
    return min(2.0 ** min(attempt, 6), 60.0)


def _post_gemini_generate_content(
    gemini_client: genai.Client,
    cfg: AppConfig,
    contents: list[types.Content],
    *,
    payload: dict,
    retries: int,
) -> str:
    """
    generate_content with retries for transient/rate errors.
    """
    last_exc: BaseException | None = None
    attempts = max(0, retries) + 1
    for attempt in range(attempts):
        try:
            response = gemini_client.models.generate_content(
                model=cfg.gemini_model,
                contents=contents,
                config=types.GenerateContentConfig(
                    temperature=float(payload["generationConfig"]["temperature"]),
                    response_mime_type=str(payload["generationConfig"]["responseMimeType"]),
                ),
            )
            if response.text:
                return response.text
            return "{}"
        except Exception as e:
            last_exc = e
            if attempt + 1 >= attempts:
                break
            e_name = type(e).__name__
            msg = str(e).lower()
            retryable = any(
                token in msg
                for token in ("429", "503", "rate", "quota", "resource exhausted", "deadline")
            )
            if not retryable and "timeout" not in e_name.lower():
                raise
            wait_s = _gemini_rate_limit_wait_s(attempt)
            print(
                f"[GEMINI] generateContent {e_name}, "
                f"retry {attempt + 1}/{retries} after {wait_s:.0f}s",
                flush=True,
            )
            time.sleep(wait_s)
    assert last_exc is not None
    raise last_exc


def compare_listing_with_gemini(
    cfg: AppConfig,
    listing_image_urls: list[str],
    http_client: httpx.Client,
) -> float:
    """
    Returns match_score in range 0..1.
    """
    if not cfg.vertex_project_id.strip():
        raise RuntimeError("Missing VERTEX_PROJECT_ID.")
    if not cfg.vertex_location.strip():
        raise RuntimeError("Missing VERTEX_LOCATION.")
    if not cfg.vertex_gcs_bucket.strip():
        raise RuntimeError("Missing VERTEX_GCS_BUCKET.")

    ref_images = _load_reference_images(cfg.reference_images)
    if not ref_images:
        raise RuntimeError("No reference_images found/readable for Gemini comparison.")

    listing_images = _load_listing_images(
        listing_image_urls,
        http_client,
        cfg.user_agent,
        cfg.max_listing_images_for_gemini,
    )
    if not listing_images:
        return 0.0

    file_uri_cache: dict[tuple[str, str], str] = {}
    gcs_client = storage.Client(project=cfg.vertex_project_id)

    # One user message = ordered parts: instructions, then section labels, then images.
    # Put all task rules and the JSON shape in the first text part; short labels only delimit blocks.
    parts: list[types.Part] = [
        types.Part.from_text(
            text=(
                "Compare the first group (REFERENCE bike photos) with the second group "
                "(LISTING photos). Determine if they likely show the same or similar physical bike. "
                "Don't judge by the brand or color so much because it could be repainted. "
                "Main factor should be whether bike is gravel type and drop bar, when you see one of them give score >0.50. "
                "Of course if you see white gravel bike give it score >0.90. "
                "If the listing is clearly NOT a gravel/cyclocross/all-road style bike (even if it's a drop-bar bike), "
                "then return a low score (<=0.15). "
                "Your score should not be equal to 0.00 if you see any similarity between the reference and the listing. "
                'Return strict JSON only: {"match_score": number_between_0_and_1}.'
            )
        ),
        types.Part.from_text(text="REFERENCE images begin"),
    ]
    for i, data in enumerate(ref_images, start=1):
        parts.append(types.Part.from_text(text=f"REFERENCE {i}"))
        part, _mode, _dt = _image_part_for_gemini(
            gcs_client,
            cfg.vertex_gcs_bucket,
            data,
            f"reference-{i}",
            file_uri_cache,
            cfg.gemini_timeout_seconds,
            use_gcs_uri=cfg.gemini_use_files_api,
        )
        parts.append(part)
    parts.append(types.Part.from_text(text="LISTING images begin"))
    for i, data in enumerate(listing_images, start=1):
        parts.append(types.Part.from_text(text=f"LISTING {i}"))
        part, _mode, _dt = _image_part_for_gemini(
            gcs_client,
            cfg.vertex_gcs_bucket,
            data,
            f"listing-{i}",
            file_uri_cache,
            cfg.gemini_timeout_seconds,
            use_gcs_uri=cfg.gemini_use_files_api,
        )
        parts.append(part)

    endpoint = _vertex_endpoint(cfg)
    payload = {
        "contents": [{"role": "user", "parts": [{"text": "<vertex-parts-omitted>"}]}],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
        },
    }
    contents = [types.Content(role="user", parts=parts)]
    _write_last_gemini_prompt(endpoint, payload)
    gen_t0 = time.monotonic()
    gemini_client = _vertex_client(cfg)
    text = _post_gemini_generate_content(
        gemini_client,
        cfg,
        contents,
        payload=payload,
        retries=cfg.gemini_generate_retries,
    )
    total_generate_s = time.monotonic() - gen_t0
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return 0.0

    raw_score = parsed.get("match_score", 0.0)
    try:
        score = max(0.0, min(1.0, float(raw_score)))
    except (TypeError, ValueError):
        score = 0.0
    return score


def compare_page_with_gemini(
    cfg: AppConfig,
    page_listings: list[Listing],
    http_client: httpx.Client,
) -> dict[str, dict]:
    """
    Compares one search-results page (batch of listings) in a single Gemini prompt.
    Returns mapping listing_id -> {"listing_data": dict, "relevance_score": float, "reason": str}.
    ``listing_data`` is left empty; matching uses images only (no titles/prices/urls in the prompt).
    """
    if not cfg.vertex_project_id.strip():
        raise RuntimeError("Missing VERTEX_PROJECT_ID.")
    if not cfg.vertex_location.strip():
        raise RuntimeError("Missing VERTEX_LOCATION.")
    if not cfg.vertex_gcs_bucket.strip():
        raise RuntimeError("Missing VERTEX_GCS_BUCKET.")

    batch_t0 = time.monotonic()
    t_ref_t0 = time.monotonic()
    ref_images = _load_reference_images(cfg.reference_images)
    if not ref_images:
        raise RuntimeError("No reference_images found/readable for Gemini comparison.")
    ref_load_s = time.monotonic() - t_ref_t0

    # List pages are typically ~30 listings; batch prompts per page to reduce API calls.
    page_listings = page_listings[:30]
    print(
        f"[GEMINI_TIMING] batch_start listings={len(page_listings)} ref_load_s={ref_load_s:.2f}",
        flush=True,
    )

    file_uri_cache: dict[tuple[str, str], str] = {}
    gcs_client = storage.Client(project=cfg.vertex_project_id)

    parts: list[types.Part] = [
        types.Part.from_text(
            text=(
                "You see only images. First group: REFERENCE bicycle. "
                "Then CANDIDATE 1, CANDIDATE 2, ... — each candidate is one or more images of the same offer. "
                "Ignore any assumption about text ads; decide solely from whether the candidate photos likely show "
                "the same physical bicycle as the reference (allow repaint/modifications). "
                "Don't judge by the brand or color so much because it could be repainted. "
                "Main factor should be whether bike is drop bar and disc brakes, when you see it give score >0.50. "
                "Of course if you see white gravel bike give it score >0.80. "
                "If the candidate is clearly NOT a gravel/cyclocross/all-road style bike, "
                "then return a low score (<=0.15). "
                "Your score should not be equal to 0.00 if you see any similarity between the reference and the listing. "
                "Return strict JSON only: "
                '{"results":[{"candidate_index":1,"relevance_score":0.0,"reason":"one short phrase, max ~25 words, why this score"}]} '
                "with one object per candidate index you were shown, relevance_score in [0,1], "
                "and reason grounded only in visible differences or similarities between images."
            )
        ),
        types.Part.from_text(text="REFERENCE images begin"),
    ]
    for i, data in enumerate(ref_images, start=1):
        parts.append(types.Part.from_text(text=f"REFERENCE {i}"))
        part, _mode, _dt = _image_part_for_gemini(
            gcs_client,
            cfg.vertex_gcs_bucket,
            data,
            f"page-ref-{i}",
            file_uri_cache,
            cfg.gemini_timeout_seconds,
            use_gcs_uri=cfg.gemini_use_files_api,
        )
        parts.append(part)

    parts.append(types.Part.from_text(text="CANDIDATE images begin"))
    candidates_shown: list[Listing] = []
    total_download_s = 0.0
    total_part_build_s = 0.0
    file_mode_counts: dict[str, int] = {"file": 0, "inline": 0, "cache": 0}
    for listing in page_listings:
        listing_dl_t0 = time.monotonic()
        listing_images = _load_listing_images(
            listing.image_urls,
            http_client,
            cfg.user_agent,
            cfg.max_listing_images_for_gemini,
        )
        listing_dl_s = time.monotonic() - listing_dl_t0
        if not listing_images:
            continue
        candidates_shown.append(listing)
        cand_idx = len(candidates_shown)
        parts.append(types.Part.from_text(text=f"CANDIDATE {cand_idx} begins"))
        listing_part_s = 0.0
        listing_part_modes: dict[str, int] = {"file": 0, "inline": 0, "cache": 0}
        for j, img_data in enumerate(listing_images, start=1):
            parts.append(types.Part.from_text(text=f"CANDIDATE {cand_idx} image {j}"))
            part, _mode, _dt = _image_part_for_gemini(
                gcs_client,
                cfg.vertex_gcs_bucket,
                img_data,
                f"page-cand-{cand_idx}-{j}",
                file_uri_cache,
                cfg.gemini_timeout_seconds,
                use_gcs_uri=cfg.gemini_use_files_api,
            )
            parts.append(part)
            listing_part_s += _dt
            listing_part_modes[_mode] = listing_part_modes.get(_mode, 0) + 1
            file_mode_counts[_mode] = file_mode_counts.get(_mode, 0) + 1

        total_download_s += listing_dl_s
        total_part_build_s += listing_part_s
        print(
            f"[GEMINI_TIMING] cand_idx={cand_idx} downloaded_imgs={len(listing_images)} dl_s={listing_dl_s:.2f} part_s={listing_part_s:.2f} modes={listing_part_modes}",
            flush=True,
        )

    endpoint = _vertex_endpoint(cfg)
    payload = {
        "contents": [{"role": "user", "parts": [{"text": "<vertex-parts-omitted>"}]}],
        "generationConfig": {
            "temperature": 0.0,
            "responseMimeType": "application/json",
        },
    }
    contents = [types.Content(role="user", parts=parts)]
    _write_last_gemini_prompt(endpoint, payload)
    gen_t0 = time.monotonic()
    gemini_client = _vertex_client(cfg)
    text = _post_gemini_generate_content(
        gemini_client,
        cfg,
        contents,
        payload=payload,
        retries=cfg.gemini_generate_retries,
    )
    total_generate_s = time.monotonic() - gen_t0
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = {}

    if isinstance(parsed, list):
        raw_results = parsed
    elif isinstance(parsed, dict):
        raw_results = parsed.get("results", [])
    else:
        raw_results = []
    if not isinstance(raw_results, list):
        raw_results = []

    out: dict[str, dict] = {}
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        raw_idx = item.get("candidate_index")
        try:
            ci = int(raw_idx)
        except (TypeError, ValueError):
            continue
        if ci < 1 or ci > len(candidates_shown):
            continue
        listing_id = candidates_shown[ci - 1].listing_id
        raw_score = item.get("relevance_score", 0.0)
        try:
            score = max(0.0, min(1.0, float(raw_score)))
        except (TypeError, ValueError):
            score = 0.0
        reason = _gemini_reason_text(item.get("reason"))
        out[listing_id] = {"listing_data": {}, "relevance_score": score, "reason": reason}

    total_s = time.monotonic() - batch_t0
    print(
        f"[GEMINI_TIMING] batch_done total_s={total_s:.2f} download_s={total_download_s:.2f} part_build_s={total_part_build_s:.2f} generate_s={total_generate_s:.2f} modes={file_mode_counts}",
        flush=True,
    )
    return out
