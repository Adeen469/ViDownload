"""MyTube feed recommendations — deterministic two-stage pipeline.

Stage 1 (candidate generation): gather videos from the user's own signals —
subscribed channels' recent uploads, most-watched channels' uploads, and
searches for the tags most common in their liked videos.
Stage 2 (ranking): score every candidate with a weighted formula
(affinity + tag match + freshness + engagement), sort, and apply a
diversity rule (max 2 videos per channel in the top 10).

The signals come from the browser's on-device data (localStorage watch
history, likes, subscriptions), POSTed to /mytube/api/recommendations.
No sign-in, no tokens — the same pipeline gets better automatically if a
YouTube account is ever connected, because the real subscriptions/likes
would simply replace the local ones as input.

Quota: one fresh computation costs roughly 10 channels x 2 units (uploads)
+ 5 tag searches x 100 units = ~530 units of the 10,000/day API quota,
so results are cached server-side for 30 minutes per signal set.
"""

import re
import time

from youtube_api import (
    YouTubeAPIError,
    api_get,
    channel_uploads,
    enrich_and_filter,
    search_videos,
)

# Ranking weights (see module docstring). Each term is scaled so the four
# contributions are comparable: a subscribed channel is worth 3 points, one
# matching tag 1 point, a just-uploaded video ~1 point, and a 2% like/view
# ratio ~1 point.
WEIGHTS = {"sub": 3.0, "tag": 1.0, "fresh": 2.0, "eng": 50.0}

MAX_SUB_CHANNELS = 10
MAX_WATCHED_CHANNELS = 3
UPLOADS_PER_CHANNEL = 4
MAX_TAGS = 5
SEARCH_PER_TAG = 8
MAX_PER_CHANNEL_TOP10 = 2
CACHE_TTL_SECONDS = 1800  # 30 minutes per signal set

_cache = {}

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_WORD_RE = re.compile(r"[a-z0-9]+")

# Words too generic to be useful as topics.
_STOPWORDS = frozenset("""
a an the and or of to in on for with by at from is are was were be been it its
this that these those as are was were will would can could should have has had
video videos watch watching new best top official full hd song songs music
""".split())


def _tokens(text):
    """Lowercase words of a title/description, minus stopwords and 1-letter bits."""
    return [w for w in _WORD_RE.findall((text or "").lower())
            if len(w) > 2 and w not in _STOPWORDS]


def _top_tags(liked_ids, history):
    """Most frequent tags of the liked videos (top 5). Falls back to the
    most frequent words in watched titles when there are no liked videos
    (or none of them carry tags)."""
    tags = []
    if liked_ids:
        try:
            data = api_get("videos", {
                "part": "snippet",
                "id": ",".join(liked_ids[:20]),
            })
            counts = {}
            for item in data.get("items", []):
                for tag in (item.get("snippet") or {}).get("tags") or []:
                    tag = (tag or "").strip().lower()
                    if tag:
                        counts[tag] = counts.get(tag, 0) + 1
            tags = [t for t, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:MAX_TAGS]]
        except YouTubeAPIError:
            tags = []
    if not tags:
        counts = {}
        for item in history or []:
            for word in _tokens(item.get("title") or ""):
                counts[word] = counts.get(word, 0) + 1
        tags = [w for w, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:MAX_TAGS]]
    return tags


def _most_watched_channels(history):
    counts = {}
    for item in history or []:
        channel_id = item.get("channel_id") or ""
        if _CHANNEL_ID_RE.match(channel_id):
            counts[channel_id] = counts.get(channel_id, 0) + 1
    return [c for c, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:MAX_WATCHED_CHANNELS]]


def _score(video, subbed_channels, top_tags):
    """Score(v) = W_sub*I_sub + W_tag*TagMatch + W_fresh/(AgeHours+2)
    + W_eng*Likes/(Views+1)."""
    score = 0.0
    if (video.get("channel_id") or "") in subbed_channels:
        score += WEIGHTS["sub"]
    haystack = ((video.get("title") or "") + " " + (video.get("description") or "")).lower()
    score += WEIGHTS["tag"] * sum(1 for tag in top_tags if tag and tag in haystack)
    published = video.get("published_at") or ""
    age_hours = None
    if published:
        try:
            from datetime import datetime, timezone
            when = datetime.fromisoformat(published.replace("Z", "+00:00"))
            age_hours = max(0.0, (datetime.now(timezone.utc) - when).total_seconds() / 3600)
        except ValueError:
            age_hours = None
    if age_hours is not None:
        score += WEIGHTS["fresh"] * (1.0 / (age_hours + 2.0))
    views = int(video.get("views") or 0)
    likes = int(video.get("likes") or 0)
    if views:
        score += WEIGHTS["eng"] * (likes / (views + 1.0))
    return score


def recommend_for(signals, max_results=24):
    """Run the pipeline for one signal set. `signals` is the JSON the
    browser POSTs: {"history": [...], "likes": {videoId: 1|-1},
    "subs": {channelId: {...}}}. Returns a list of mapped video dicts."""
    signals = signals or {}
    history = signals.get("history") or []
    likes = signals.get("likes") or {}
    subs = signals.get("subs") or {}
    if not isinstance(history, list):
        history = []
    if not isinstance(likes, dict):
        likes = {}
    if not isinstance(subs, dict):
        subs = {}

    liked_ids = [vid for vid, vote in likes.items()
                 if vote == 1 and _VIDEO_ID_RE.match(str(vid))][:20]
    disliked_ids = {str(vid) for vid, vote in likes.items() if vote == -1}
    sub_ids = [ch for ch in subs.keys() if _CHANNEL_ID_RE.match(str(ch))][:MAX_SUB_CHANNELS]
    history_ids = [str(item.get("id")) for item in history
                   if isinstance(item, dict) and _VIDEO_ID_RE.match(str(item.get("id") or ""))][:20]
    if not liked_ids and not sub_ids and not history_ids:
        return []

    cache_key = (tuple(sorted(liked_ids)), tuple(sorted(sub_ids)), tuple(history_ids))
    now = time.time()
    cached = _cache.get(cache_key)
    if cached and cached[0] > now:
        return list(cached[1][:max_results])

    top_tags = _top_tags(liked_ids, history)

    # ---- Stage 1: candidate generation ----
    candidates = {}
    for channel_id in sub_ids:
        for video in channel_uploads(channel_id, max_results=UPLOADS_PER_CHANNEL):
            candidates.setdefault(video["id"], video)
    for channel_id in _most_watched_channels(history):
        for video in channel_uploads(channel_id, max_results=UPLOADS_PER_CHANNEL):
            candidates.setdefault(video["id"], video)
    for tag in top_tags:
        for video in search_videos(tag, max_results=SEARCH_PER_TAG):
            candidates.setdefault(video["id"], video)
    for video_id in disliked_ids:
        candidates.pop(video_id, None)
    if not candidates:
        return []

    # ---- Stage 2: ranking ----
    subbed = set(sub_ids)
    enriched = enrich_and_filter(list(candidates.values()))
    ranked = sorted(enriched, key=lambda v: _score(v, subbed, top_tags), reverse=True)

    # Diversity: max 2 videos per channel in the top 10; the rest follow
    # in ranked order after that.
    result, per_channel, rest = [], {}, []
    for video in ranked:
        channel_id = video.get("channel_id") or ""
        if len(result) < 10 and per_channel.get(channel_id, 0) < MAX_PER_CHANNEL_TOP10:
            per_channel[channel_id] = per_channel.get(channel_id, 0) + 1
            result.append(video)
        else:
            rest.append(video)
    result.extend(rest[: max_results - len(result)])
    result = result[:max_results]

    _cache[cache_key] = (now + CACHE_TTL_SECONDS, result)
    return list(result)
