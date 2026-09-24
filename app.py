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
import shutil
import tempfile
import uuid
from pathlib import Path

import yt_dlp
from flask import Flask, after_this_request, jsonify, render_template, request, send_file

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Files are staged here just long enough to stream them to the browser, then deleted.
DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "video_downloader_tmp"
DOWNLOAD_DIR.mkdir(exist_ok=True)

# This project is scoped to platforms downloading is commonly permitted for
# (your own uploads, Creative Commons / public-domain content, fair use clips).
# yt-dlp itself understands far more sites, but this app only exposes these.
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
]


def is_supported(url: str) -> bool:
    return any(domain in url.lower() for domain in SUPPORTED_DOMAINS)


def human_size(num_bytes):
    if not num_bytes:
        return "size unknown"
    size = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


@app.route("/")
def index():
    return render_template("index.html")


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
        if f.get("acodec") == "none":
            format_id = f"{format_id}+bestaudio/best"

        formats.append({
            "format_id": format_id,
            "label": " · ".join(label_parts) if label_parts else (f.get("format_id") or "unknown"),
            "filesize": human_size(f.get("filesize") or f.get("filesize_approx")),
            "height": f.get("height") or 0,
            "audio_only": is_audio_only,
        })

    formats.sort(key=lambda x: (x["audio_only"], -x["height"]))

    return jsonify({
        "title": info.get("title"),
        "thumbnail": info.get("thumbnail"),
        "uploader": info.get("uploader"),
        "duration": info.get("duration"),
        "formats": formats,
    })


@app.route("/api/download", methods=["POST"])
def download():
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
        # Video-only choices already include a best-audio fallback from /api/info.
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


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)