#!/usr/bin/env python3
"""
Video Downloader — Educational Project
Backend: Flask + yt-dlp

Flow:
  1. User pastes a URL -> POST /api/info -> we return title, thumbnail,
     uploader, duration, and a list of available quality options.
  2. User picks a quality -> POST /api/download -> we download that exact
     format with yt-dlp and stream the file back to the browser.
"""

import logging
import re
import shutil
import tempfile
import threading
import uuid
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from flask import Flask, after_this_request, jsonify, redirect, render_template, request, send_file

import youtube_api
from youtube_api import YouTubeAPIError

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Files are staged here just long enough to stream them to the browser, then deleted.
DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "video_downloader_tmp"
DOWNLOAD_DIR.mkdir(exist_ok=True)
DOWNLOAD_JOBS = {}
DOWNLOAD_JOBS_LOCK = threading.Lock()

# This project is scoped to platforms downloading is commonly permitted for
# (your own uploads, Creative Commons / public-domain content, fair use clips).
# yt-dlp itself understands far more sites, but this app only exposes this
# curated set. Availability can still vary by video, region, and login rules.
SUPPORTED_DOMAINS = [
    "youtube.com", "youtu.be", "music.youtube.com",
    "instagram.com",
    "facebook.com", "fb.watch",
    "twitter.com", "x.com",
    "linkedin.com",
    "reddit.com",
    "tiktok.com",
    "vimeo.com",
    "dailymotion.com",
    "twitch.tv",
    "xhamster.com",
    "pornhub.com",
    "eporner.com",
    "xvideos.com",
    "xnxx.com",
    "youporn.com",
    "redgifs.com",
    "spankbang.com",
    "tube8.com",
    "youjizz.com",
    "chaturbate.com",
    "motherless.com",
    "yesporn.vip",
]
SUPPORTED_HOST_PATTERNS = [
    re.compile(r"^xhamster\d*\.com$"),
    re.compile(r"^xhamster\d*\.desi$"),
]


def is_supported(url: str) -> bool:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme in {"http", "https"} and (
        any(
            hostname == domain or hostname.endswith(f".{domain}")
            for domain in SUPPORTED_DOMAINS
        )
        or any(pattern.fullmatch(hostname) for pattern in SUPPORTED_HOST_PATTERNS)
    )


def human_size(num_bytes):
    if not num_bytes:
        return "size unknown"
    size = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def estimated_size(stream, duration, fallback_size=0):
    size = stream.get("filesize") or stream.get("filesize_approx") or fallback_size
    if size:
        return size, False
    bitrate = stream.get("tbr") or stream.get("abr") or 0
    if not bitrate and stream.get("height"):
        bitrate = max(1200, min(20000, stream["height"] * 8))
    return (int(bitrate * 1000 / 8 * duration), True) if bitrate and duration else (0, False)


def create_download_job(url, format_id):
    job_id = uuid.uuid4().hex
    job_dir = DOWNLOAD_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    with DOWNLOAD_JOBS_LOCK:
        DOWNLOAD_JOBS[job_id] = {"status": "preparing", "progress": 0, "path": None, "error": None}

    def run():
        def progress_hook(data):
            if data.get("status") != "downloading":
                return
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            downloaded = data.get("downloaded_bytes") or 0
            progress = int(downloaded / total * 90) if total else 5
            with DOWNLOAD_JOBS_LOCK:
                DOWNLOAD_JOBS[job_id]["progress"] = min(90, max(5, progress))

        ydl_opts = {
            "format": format_id,
            "outtmpl": str(job_dir / "%(title).150B.%(ext)s"),
            "merge_output_format": "mp4",
            "progress_hooks": [progress_hook],
            "quiet": True,
            "no_warnings": True,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                with DOWNLOAD_JOBS_LOCK:
                    DOWNLOAD_JOBS[job_id]["progress"] = 5
                info = ydl.extract_info(url, download=True)
                filepath = Path(ydl.prepare_filename(info))
                if not filepath.exists():
                    merged_files = [path for path in job_dir.iterdir() if path.is_file()]
                    if merged_files:
                        filepath = merged_files[0]
            if not filepath.exists():
                raise FileNotFoundError("output file missing")
            with DOWNLOAD_JOBS_LOCK:
                DOWNLOAD_JOBS[job_id].update({"status": "ready", "progress": 100, "path": filepath})
        except Exception as exc:
            shutil.rmtree(job_dir, ignore_errors=True)
            with DOWNLOAD_JOBS_LOCK:
                DOWNLOAD_JOBS[job_id].update({"status": "error", "error": str(exc)})

    threading.Thread(target=run, daemon=True).start()
    return job_id


@app.route("/")
def index():
    return render_template("landing.html")


@app.route("/vidownloads")
def vidownloads():
    return render_template("index.html")


# ---- MyTube (YouTube clone) ----
# Embeds only: videos play through the official YouTube iframe player.
# No YouTube stream is ever extracted or downloaded here.

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
CHANNEL_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _mytube_context(active="home", **extra):
    context = {"api_missing": not youtube_api.is_configured(), "api_error": None, "active": active}
    context.update(extra)
    return context


def _category_chips(selected_label):
    chips = []
    for category in youtube_api.CATEGORIES:
        chips.append({
            "label": category["label"],
            "param": "" if category["kind"] == "all" else category["label"],
            "active": category["label"].lower() == (selected_label or "all").lower(),
        })
    return chips


@app.route("/mytube")
def mytube_home():
    category_param = (request.args.get("category") or "").strip()
    selected = next(
        (c for c in youtube_api.CATEGORIES
         if c["label"].lower() == category_param.lower()
         or (c["value"] and c["value"] == category_param)),
        youtube_api.CATEGORIES[0],
    )
    context = _mytube_context(
        active="home",
        categories=_category_chips(selected["label"]),
        heading="Recommended",
        videos=[],
        extra_rows=[],
    )
    if not youtube_api.is_configured():
        return render_template("mytube/home.html", **context)
    try:
        if selected["kind"] == "all":
            videos = youtube_api.trending(max_results=24)
            extra_rows = [
                {"heading": "Music", "videos": youtube_api.trending(category_id="10", max_results=8)},
                {"heading": "Gaming", "videos": youtube_api.trending(category_id="20", max_results=8)},
            ]
            context.update(videos=videos, extra_rows=extra_rows)
        elif selected["kind"] == "category":
            videos = youtube_api.trending(category_id=selected["value"], max_results=24)
            context.update(heading=f"{selected['label']} — trending", videos=videos)
        else:  # search-based chip (e.g. Cooking has no category id)
            videos = youtube_api.search_videos(selected["value"], max_results=24)
            context.update(heading=selected["label"], videos=videos)
    except YouTubeAPIError as exc:
        context["api_error"] = str(exc)
    return render_template("mytube/home.html", **context)


@app.route("/mytube/trending")
def mytube_trending():
    context = _mytube_context(active="trending", videos=[])
    if not youtube_api.is_configured():
        return render_template("mytube/trending.html", **context)
    try:
        context["videos"] = youtube_api.trending(max_results=24)
    except YouTubeAPIError as exc:
        context["api_error"] = str(exc)
    return render_template("mytube/trending.html", **context)


@app.route("/mytube/search")
def mytube_search():
    query = (request.args.get("q") or "").strip()
    if not query:
        return redirect("/mytube")
    context = _mytube_context(active="home", q=query, videos=[])
    if not youtube_api.is_configured():
        return render_template("mytube/search.html", **context)
    try:
        context["videos"] = youtube_api.search_videos(query, max_results=24)
    except YouTubeAPIError as exc:
        context["api_error"] = str(exc)
    return render_template("mytube/search.html", **context)


@app.route("/mytube/watch")
def mytube_watch():
    video_id = (request.args.get("v") or "").strip()
    context = _mytube_context(active="home", video=None, related=[])
    if not VIDEO_ID_RE.match(video_id):
        return render_template("mytube/watch.html", **context), 404
    if not youtube_api.is_configured():
        return render_template("mytube/watch.html", **context)
    try:
        video = youtube_api.video_details(video_id)
    except YouTubeAPIError as exc:
        context["api_error"] = str(exc)
        return render_template("mytube/watch.html", **context)
    if not video:
        return render_template("mytube/watch.html", **context), 404
    try:
        related = youtube_api.related_videos(video_id)
    except YouTubeAPIError:
        # "Up next" is optional — never let it kill the player. Fall back
        # to trending so the sidebar is not empty.
        try:
            related = youtube_api.trending(max_results=12)
        except YouTubeAPIError:
            related = []
    context.update(video=video, related=related)
    return render_template("mytube/watch.html", **context)


@app.route("/mytube/channel/<channel_id>")
def mytube_channel(channel_id):
    context = _mytube_context(active="home", channel=None, videos=[])
    if not CHANNEL_ID_RE.match(channel_id):
        return render_template("mytube/channel.html", **context), 404
    if not youtube_api.is_configured():
        return render_template("mytube/channel.html", **context)
    try:
        channel = youtube_api.channel_details(channel_id)
        videos = youtube_api.channel_uploads(channel_id) if channel else []
    except YouTubeAPIError as exc:
        context["api_error"] = str(exc)
        return render_template("mytube/channel.html", **context)
    if not channel:
        return render_template("mytube/channel.html", **context), 404
    context.update(channel=channel, videos=videos)
    return render_template("mytube/channel.html", **context)


@app.route("/mytube/history")
def mytube_history():
    # History lives in the browser (localStorage); the server renders the shell.
    return render_template("mytube/history.html", **_mytube_context(active="history"))


@app.route("/mytube/subscriptions")
def mytube_subscriptions():
    # Subscriptions live in the browser (localStorage); the server renders the shell.
    return render_template("mytube/subscriptions.html", **_mytube_context(active="subscriptions"))


@app.route("/mytube/api/channel-videos")
def mytube_api_channel_videos():
    """JSON: latest uploads of one channel, for the home page's
    'Because you watched' row (driven by browser watch history)."""
    channel_id = (request.args.get("channel_id") or "").strip()
    if not CHANNEL_ID_RE.match(channel_id):
        return jsonify({"error": "Invalid channel id."}), 400
    if not youtube_api.is_configured():
        return jsonify({"error": "YOUTUBE_API_KEY is not set.", "api_missing": True})
    try:
        videos = youtube_api.channel_uploads(channel_id, max_results=8)
    except YouTubeAPIError as exc:
        return jsonify({"error": str(exc)})
    return jsonify({"videos": videos})


@app.route("/api/info", methods=["POST"])
def get_info():
    data = request.get_json(silent=True) or {}
    url = data.get("url", "").strip()

    if not url:
        return jsonify({"error": "Paste a video URL first."}), 400
    if not is_supported(url):
        return jsonify({"error": "That platform isn't enabled in this project."}), 400

    ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True}

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        logger.warning("info fetch failed for %s: %s", url, exc)
        return jsonify({"error": "Couldn't read that link. It may be private, deleted, or region-locked."}), 400

    duration = info.get("duration") or 0
    audio_formats = [
        audio for audio in info.get("formats", [])
        if audio.get("vcodec") == "none" and audio.get("acodec") != "none"
    ]
    best_audio = max(audio_formats, key=lambda audio: audio.get("abr") or audio.get("tbr") or 0, default={})
    best_audio_size, best_audio_estimated = estimated_size(best_audio, duration)
    formats = []
    for f in info.get("formats", []):
        # Skip entries with no playable stream at all (e.g. storyboard/thumbnail tracks).
        if f.get("vcodec") == "none" and f.get("acodec") == "none":
            continue

        is_audio_only = f.get("vcodec") == "none"
        label_parts = []
        if f.get("height"):
            label_parts.append(f"{f['height']}p")
        if is_audio_only:
            label_parts.append("audio only")
        if f.get("ext"):
            label_parts.append(f["ext"])

        if is_audio_only:
            continue

        format_id = f.get("format_id") or ""
        is_video_only = f.get("acodec") == "none"
        if is_video_only:
            format_id = f"{format_id}+bestaudio/best"

        video_size, video_estimated = estimated_size(f, duration)
        total_size = video_size + best_audio_size if is_video_only else video_size
        size_label = human_size(total_size)
        if total_size and (video_estimated or (is_video_only and best_audio_estimated)):
            size_label += " (estimated)"

        formats.append({
            "format_id": format_id,
            "label": " · ".join(label_parts) if label_parts else (f.get("format_id") or "unknown"),
            "filesize": size_label,
            "height": f.get("height") or 0,
            "audio_only": is_audio_only,
            "ext": f.get("ext") or "",
            "size_bytes": total_size,
        })

    # YouTube reports several streams per quality (different codecs and
    # bitrates share one "720p · mp4" label). Keep only the biggest stream
    # per (height, ext) so each quality appears once.
    best_by_quality = {}
    for entry in formats:
        key = (entry["height"], entry["ext"])
        if key not in best_by_quality or entry["size_bytes"] > best_by_quality[key]["size_bytes"]:
            best_by_quality[key] = entry
    formats = [
        {k: v for k, v in entry.items() if k not in ("size_bytes", "ext")}
        for entry in best_by_quality.values()
    ]

    formats.sort(key=lambda x: (x["audio_only"], -x["height"]))

    return jsonify({
        "title": info.get("title"),
        "thumbnail": info.get("thumbnail"),
        "uploader": info.get("uploader"),
        "duration": info.get("duration"),
        "formats": formats,
    })


@app.route("/api/download", methods=["GET", "POST"])
def download():
    if request.method == "GET":
        url = request.args.get("url", "").strip()
        format_id = request.args.get("format_id", "").strip()
    else:
        data = request.get_json(silent=True) or {}
        url = data.get("url", "").strip()
        format_id = data.get("format_id", "").strip()

    if not url or not format_id:
        return jsonify({"error": "Missing url or format_id."}), 400
    if not is_supported(url):
        return jsonify({"error": "That platform isn't enabled in this project."}), 400

    job_dir = DOWNLOAD_DIR / uuid.uuid4().hex
    job_dir.mkdir(parents=True, exist_ok=True)

    ydl_opts = {
        "format": format_id,
        "outtmpl": str(job_dir / "%(title).150B.%(ext)s"),
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filepath = Path(ydl.prepare_filename(info))
            if not filepath.exists():
                merged_files = [path for path in job_dir.iterdir() if path.is_file()]
                if merged_files:
                    filepath = merged_files[0]
    except Exception as exc:
        shutil.rmtree(job_dir, ignore_errors=True)
        logger.warning("download failed for %s (%s): %s", url, format_id, exc)
        return jsonify({"error": "Download failed. Try a different quality."}), 500

    if not filepath.exists():
        shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify({"error": "Download failed — output file missing."}), 500

    @after_this_request
    def cleanup(response):
        shutil.rmtree(job_dir, ignore_errors=True)
        return response

    return send_file(filepath, as_attachment=True, download_name=filepath.name)


@app.route("/api/download/start", methods=["POST"])
def start_download():
    data = request.get_json(silent=True) or {}
    url = data.get("url", "").strip()
    format_id = data.get("format_id", "").strip()
    if not url or not format_id:
        return jsonify({"error": "Missing url or format_id."}), 400
    if not is_supported(url):
        return jsonify({"error": "That platform isn't enabled in this project."}), 400
    return jsonify({"job_id": create_download_job(url, format_id)})


@app.route("/api/download/status/<job_id>")
def download_status(job_id):
    with DOWNLOAD_JOBS_LOCK:
        job = DOWNLOAD_JOBS.get(job_id)
        if not job:
            return jsonify({"error": "Download job not found."}), 404
        return jsonify({"status": job["status"], "progress": job["progress"], "error": job["error"]})


@app.route("/api/download/file/<job_id>")
def download_file(job_id):
    with DOWNLOAD_JOBS_LOCK:
        job = DOWNLOAD_JOBS.get(job_id)
        filepath = job.get("path") if job else None
    if not job:
        return jsonify({"error": "Download job not found."}), 404
    if job["status"] != "ready" or not filepath:
        return jsonify({"error": "Download is not ready."}), 409

    @after_this_request
    def cleanup(response):
        shutil.rmtree(filepath.parent, ignore_errors=True)
        with DOWNLOAD_JOBS_LOCK:
            DOWNLOAD_JOBS.pop(job_id, None)
        return response

    return send_file(filepath, as_attachment=True, download_name=filepath.name)


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)