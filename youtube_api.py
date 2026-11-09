#!/usr/bin/env python3
"""
MyTube — thin YouTube Data API v3 client.

Uses only the standard library (urllib), no new dependencies.

Conventions (same as the My Music app):
- regionCode "IN" everywhere, so results match what plays in India.
- search.list uses type=video, videoEmbeddable=true, safeSearch=moderate.
- trending uses videos.list chart=mostPopular (cost 1 unit, vs 100 for
  search.list), with an optional videoCategoryId for the category chips.
- Every list is filtered by the India-availability rules My Music uses:
  public videos only, embeddable only, not region-blocked in IN.
  If the availability check itself fails, lists are returned unfiltered
  rather than blanking the page (fail open).

Quota: search.list costs 100 units per call (default daily quota 10,000),
videos.list / channels.list / playlistItems.list cost 1. The home page
therefore uses mostPopular (cheap); search.list is only used for user
searches, related videos, and the Cooking chip (which has no category id).

The API key is read from the YOUTUBE_API_KEY environment variable at call
time and is NEVER logged or returned to the browser. When it is missing,
every function raises YouTubeAPIError(503) and the routes render a
friendly on-page message instead of a 500.
"""

import html
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API_BASE = "https://www.googleapis.com/youtube/v3"
CACHE_TTL_SECONDS = 300  # 5 minutes per query, to save quota

_cache = {}
_cache_lock = threading.Lock()

# Category chips on the MyTube home page. "kind" is "category" (YouTube
# videoCategoryId, cheap mostPopular call) or "search" (no category id
# exists, so a search.list query is used instead).
CATEGORIES = [
    {"label": "All", "kind": "all", "value": None},
    {"label": "Music", "kind": "category", "value": "10"},
    {"label": "Gaming", "kind": "category", "value": "20"},
    {"label": "News", "kind": "category", "value": "25"},
    {"label": "Sports", "kind": "category", "value": "17"},
    {"label": "Comedy", "kind": "category", "value": "23"},
    {"label": "Tech", "kind": "category", "value": "28"},
    {"label": "Cooking", "kind": "search", "value": "cooking"},
    {"label": "Travel", "kind": "category", "value": "19"},
    {"label": "Education", "kind": "category", "value": "27"},
    {"label": "Entertainment", "kind": "category", "value": "24"},
    {"label": "Film", "kind": "category", "value": "1"},
]


class YouTubeAPIError(Exception):
    """Raised for any YouTube API problem; .status is the HTTP status (if any)."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def api_key():
    return (os.environ.get("YOUTUBE_API_KEY") or "").strip()


def is_configured():
    return bool(api_key())


def _cache_get(key):
    with _cache_lock:
        entry = _cache.get(key)
        if entry and entry[0] > time.time():
            return entry[1]
        _cache.pop(key, None)
        return None


def _cache_set(key, value):
    with _cache_lock:
        _cache[key] = (time.time() + CACHE_TTL_SECONDS, value)


def api_get(pathname, params):
    """GET one YouTube Data API endpoint. `params` excludes the key.

    The key is never part of the cache key and never appears in any
    error message or log line.
    """
    key = api_key()
    if not key:
        raise YouTubeAPIError("YOUTUBE_API_KEY is not set.", status=503)
    cache_key = pathname + "?" + urllib.parse.urlencode(sorted(params.items()))
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    query = dict(params)
    query["key"] = key
    url = API_BASE + "/" + pathname + "?" + urllib.parse.urlencode(query)
    req = urllib.request.Request(url, headers={"User-Agent": "ViDownload-MyTube/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
            message = (body.get("error") or {}).get("message") or f"YouTube API error {exc.code}"
        except Exception:
            message = f"YouTube API error {exc.code}"
        raise YouTubeAPIError(message, status=exc.code)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise YouTubeAPIError(f"Could not reach the YouTube API: {exc}", status=502)
    _cache_set(cache_key, data)
    return data


# ---- formatting helpers ----

_DURATION_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def parse_duration(iso):
    """ISO 8601 duration (PT1H2M3S) -> seconds."""
    if not iso:
        return 0
    match = _DURATION_RE.fullmatch(iso)
    if not match:
        return 0
    hours, minutes, seconds = (int(x) if x else 0 for x in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def format_duration(seconds):
    seconds = int(seconds or 0)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _trim(value):
    return value[:-2] if value.endswith(".0") else value


def format_count(number):
    try:
        number = int(number)
    except (TypeError, ValueError):
        return "0"
    if number >= 1_000_000_000:
        return _trim(f"{number / 1e9:.1f}") + "B"
    if number >= 1_000_000:
        return _trim(f"{number / 1e6:.1f}") + "M"
    if number >= 1_000:
        return _trim(f"{number / 1e3:.1f}") + "K"
    return str(number)


def format_age(published_at):
    """RFC 3339 timestamp -> '3 days ago' style string."""
    if not published_at:
        return ""
    try:
        when = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return ""
    seconds = max(0, int((datetime.now(timezone.utc) - when).total_seconds()))
    if seconds < 3600:
        return f"{max(1, seconds // 60)} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} hours ago"
    if seconds < 86400 * 30:
        return f"{seconds // 86400} days ago"
    if seconds < 86400 * 365:
        return f"{seconds // (86400 * 30)} months ago"
    return f"{seconds // (86400 * 365)} years ago"


def _pick_thumbnail(thumbnails):
    """Best card thumbnail: medium (320x180) first, then high, default."""
    thumbs = thumbnails or {}
    for quality in ("medium", "high", "standard", "maxres", "default"):
        url = (thumbs.get(quality) or {}).get("url")
        if url:
            return url
    return ""


def _clean(text):
    """YouTube returns HTML-escaped titles; unescape once so Jinja's
    autoescaping shows them correctly instead of double-escaping."""
    return html.unescape(text or "")


# ---- mapping ----

def map_video(item):
    """One videos.list / search.list item -> a plain dict for templates."""
    raw_id = item.get("id")
    video_id = raw_id if isinstance(raw_id, str) else (raw_id or {}).get("videoId", "")
    snippet = item.get("snippet") or {}
    statistics = item.get("statistics") or {}
    content_details = item.get("contentDetails") or {}
    duration = parse_duration(content_details.get("duration"))
    views = int(statistics.get("viewCount") or 0)
    return {
        "id": video_id,
        "title": _clean(snippet.get("title")),
        "channel": _clean(snippet.get("channelTitle")),
        "channel_id": snippet.get("channelId") or "",
        "thumbnail": _pick_thumbnail(snippet.get("thumbnails")),
        "published_at": snippet.get("publishedAt") or "",
        "age": format_age(snippet.get("publishedAt")),
        "duration": duration,
        "duration_label": format_duration(duration) if duration else "",
        "views": views,
        "views_label": f"{format_count(views)} views" if views else "",
        "likes": int(statistics.get("likeCount") or 0),
        "likes_label": format_count(statistics.get("likeCount")) if statistics.get("likeCount") else "",
        "description": _clean(snippet.get("description")),
    }


def map_channel(item):
    snippet = item.get("snippet") or {}
    statistics = item.get("statistics") or {}
    content_details = item.get("contentDetails") or {}
    playlists = content_details.get("relatedPlaylists") or {}
    banner = ((item.get("brandingSettings") or {}).get("image") or {}).get("bannerExternalUrl") or ""
    subs = int(statistics.get("subscriberCount") or 0)
    return {
        "id": item.get("id") or "",
        "title": _clean(snippet.get("title")),
        "thumbnail": _pick_thumbnail(snippet.get("thumbnails")),
        "banner": banner,
        "subs": subs,
        "subs_label": f"{format_count(subs)} subscribers" if subs else "",
        "description": _clean(snippet.get("description")),
        "uploads_playlist": playlists.get("uploads") or "",
    }


def is_available(detail):
    """India-availability rules (same as the My Music app)."""
    if not detail:
        return False
    status = detail.get("status") or {}
    if status.get("privacyStatus") and status["privacyStatus"] != "public":
        return False
    if status.get("embeddable") is False:
        return False
    restriction = (detail.get("contentDetails") or {}).get("regionRestriction") or {}
    if "IN" in (restriction.get("blocked") or []):
        return False
    allowed = restriction.get("allowed")
    if allowed is not None and "IN" not in allowed:
        return False
    return True


def enrich_and_filter(items):
    """Fill durations/views for search results and drop videos that
    cannot play in India. Fails open: on any API error the list is
    returned as-is rather than blanking the page."""
    if not items:
        return items
    ids = [item["id"] for item in items if item.get("id")]
    if not ids:
        return items
    try:
        data = api_get("videos", {
            "part": "status,contentDetails,statistics",
            "id": ",".join(ids[:50]),
        })
        by_id = {item.get("id"): item for item in data.get("items", [])}
    except YouTubeAPIError:
        return items
    kept = []
    for item in items:
        detail = by_id.get(item["id"])
        if detail is None:
            kept.append(item)  # video vanished from the API: keep, player will say so
            continue
        if not is_available(detail):
            continue
        if not item.get("duration"):
            duration = parse_duration((detail.get("contentDetails") or {}).get("duration"))
            item["duration"] = duration
            item["duration_label"] = format_duration(duration) if duration else ""
        stats = detail.get("statistics") or {}
        if stats.get("viewCount") and not item.get("views"):
            item["views"] = int(stats["viewCount"])
            item["views_label"] = f"{format_count(stats['viewCount'])} views"
        if stats.get("likeCount"):
            item["likes"] = int(stats["likeCount"])
            item["likes_label"] = format_count(stats["likeCount"])
        kept.append(item)
    return kept


# ---- public API used by the routes ----

def trending(category_id=None, max_results=24):
    """Most-popular videos, optionally within one category (cost: 1 unit)."""
    params = {
        "part": "snippet,statistics,contentDetails,status",
        "chart": "mostPopular",
        "regionCode": "IN",
        "maxResults": str(max_results),
    }
    if category_id:
        params["videoCategoryId"] = str(category_id)
    data = api_get("videos", params)
    return [map_video(item) for item in data.get("items", []) if is_available(item)]


def search_videos(query, max_results=24):
    """User search (cost: 100 units — the expensive one, use sparingly)."""
    data = api_get("search", {
        "part": "snippet",
        "type": "video",
        "q": query,
        "maxResults": str(max_results),
        "order": "relevance",
        "regionCode": "IN",
        "videoEmbeddable": "true",
        "safeSearch": "moderate",
    })
    return enrich_and_filter([map_video(item) for item in data.get("items", [])])


def video_details(video_id):
    """Full details for one video, or None if it does not exist / cannot play."""
    data = api_get("videos", {
        "part": "snippet,statistics,contentDetails,status",
        "id": video_id,
    })
    items = data.get("items", [])
    if not items or not is_available(items[0]):
        return None
    return map_video(items[0])


def related_videos(video_id, max_results=12):
    """Videos related to one video (cost: 100 units).

    NOTE: only part/type/relatedToVideoId/maxResults/regionCode are sent.
    YouTube rejects relatedToVideoId combined with the video* filter
    params (videoEmbeddable, safeSearch, etc.) with 400 "Request contains
    an invalid argument." — the exact failure every watch page hit. The
    enrich_and_filter step below still drops non-embeddable videos, so
    Up next stays playable without those filters.
    """
    data = api_get("search", {
        "part": "snippet",
        "type": "video",
        "relatedToVideoId": video_id,
        "maxResults": str(max_results),
        "regionCode": "IN",
    })
    return enrich_and_filter([map_video(item) for item in data.get("items", [])])


def channel_details(channel_id):
    """Channel info, or None if it does not exist."""
    data = api_get("channels", {
        "part": "snippet,statistics,contentDetails,brandingSettings",
        "id": channel_id,
    })
    items = data.get("items", [])
    if not items:
        return None
    return map_channel(items[0])


def channel_uploads(channel_id, max_results=24):
    """Latest uploads of one channel (cost: ~2 units)."""
    channel = channel_details(channel_id)
    if not channel or not channel.get("uploads_playlist"):
        return []
    data = api_get("playlistItems", {
        "part": "snippet,contentDetails",
        "playlistId": channel["uploads_playlist"],
        "maxResults": str(max_results),
    })
    items = []
    for entry in data.get("items", []):
        snippet = entry.get("snippet") or {}
        content_details = entry.get("contentDetails") or {}
        video_id = content_details.get("videoId") or (snippet.get("resourceId") or {}).get("videoId")
        if not video_id:
            continue
        items.append({
            "id": video_id,
            "title": _clean(snippet.get("title")),
            "channel": _clean(snippet.get("channelTitle")),
            "channel_id": snippet.get("channelId") or channel_id,
            "thumbnail": _pick_thumbnail(snippet.get("thumbnails")),
            "published_at": snippet.get("publishedAt") or "",
            "age": format_age(snippet.get("publishedAt")),
            "duration": 0,
            "duration_label": "",
            "views": 0,
            "views_label": "",
            "likes": 0,
            "likes_label": "",
            "description": _clean(snippet.get("description")),
        })
    return enrich_and_filter(items)
