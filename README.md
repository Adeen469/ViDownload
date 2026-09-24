# Video Downloader — College Project

A small web app: paste a video URL, it fetches the title, thumbnail, uploader,
and available quality options, and you pick one to download.

**Stack:** Flask (backend) + [yt-dlp](https://github.com/yt-dlp/yt-dlp) (extraction/download) + vanilla HTML/CSS/JS (frontend).

## Supported platforms

YouTube, Instagram, Facebook, X/Twitter, LinkedIn, Reddit, TikTok, Vimeo,
Dailymotion, Twitch. This list is deliberately curated (see `SUPPORTED_DOMAINS`
in `app.py`) — it isn't every site yt-dlp can theoretically reach.

## Setup

```bash
python -m venv venv
source venv/bin/activate   # venv\Scripts\activate on Windows
pip install -r requirements.txt
```

FFmpeg is optional but recommended if you want yt-dlp to merge separate
video+audio streams into one file for the highest qualities:

```bash
# macOS
brew install ffmpeg
# Ubuntu/Debian
sudo apt-get install ffmpeg
# Windows
choco install ffmpeg
```

## Run

```bash
python app.py
```

Then open http://localhost:5000 in a browser.

## How it works

- `POST /api/info` — calls `yt_dlp.YoutubeDL().extract_info(url, download=False)`
  to pull metadata without downloading anything, filters the format list down
  to playable video/audio streams, and returns title, thumbnail, uploader,
  duration, and a sorted list of `{format_id, label, filesize}` options.
- `POST /api/download` — re-runs extraction with `download=True` and
  `format` pinned to the `format_id` the user picked, saves to a per-request
  temp folder, streams the file back with `send_file`, then deletes the temp
  folder.

## Project structure

```
webapp/
├── app.py                  # Flask routes + yt-dlp calls
├── requirements.txt
├── templates/
│   └── index.html          # page shell
└── static/
    ├── style.css
    └── script.js            # fetch info, render quality list, trigger download
```

## Scope and limits, for the write-up

- This app only lists formats yt-dlp itself reports as available for a given
  link — it does not re-encode, upscale, or otherwise create quality that
  wasn't in the source.
- No accounts, cookies, or login are wired in, so anything behind a login
  wall (private videos, friends-only posts) won't resolve.
- Downloaded files are held in a temp directory only for the duration of the
  request and deleted immediately after.

## A note on legality

Downloading is easy to build; whether it's *allowed* depends entirely on the
source and your rights to it. Most platforms' terms of service restrict
downloading video from their site, regardless of what's technically possible.
Treat this project as a demonstration of the mechanics (metadata extraction,
format negotiation, file streaming) — for real use, stick to content you own,
that's under a license permitting reuse (e.g. Creative Commons), or that the
platform explicitly allows saving (some let creators enable downloads).