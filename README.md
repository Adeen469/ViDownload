# Video Downloader — College Project

A small web app with two halves: **MyTube** (a YouTube-style video
experience) and **Vidownloads** (download videos from supported
platforms). The landing page at `/` offers both as two cards.

**Stack:** Flask (backend) + [yt-dlp](https://github.com/yt-dlp/yt-dlp) (extraction/download) + YouTube Data API v3 (MyTube) + vanilla HTML/CSS/JS (frontend).

## MyTube

Watch, search, trending and personalised recommendations — videos play
through the official YouTube iframe embed player. **No YouTube stream
is ever extracted or downloaded**; MyTube is embeds only.

Routes: `/mytube` (home + category chips + recommendations),
`/mytube/search?q=...`, `/mytube/watch?v=...` (player + Up next +
autoplay-next), `/mytube/channel/<id>`, `/mytube/trending`,
`/mytube/history` and `/mytube/subscriptions` (both driven by browser
localStorage — no account, nothing is uploaded).

Recommendations mix YouTube trending (`mostPopular`, region IN) with
category picks. When the browser has watch history, videos from
watched channels are boosted to the front and a "Because you watched
X" row shows that channel's latest uploads.

### Setup — MyTube needs a YouTube API key

```bash
export YOUTUBE_API_KEY="your-key-here"
```

Get one free at [Google Cloud Console](https://console.cloud.google.com/apis/credentials) (enable the **YouTube Data API v3**). Without the key, every MyTube page shows "Add YOUTUBE_API_KEY to run MyTube" instead of failing — the downloader still works.

**Quota note:** the free default quota is 10,000 units/day. `search.list`
costs 100 units per call, while `videos.list` / `channels.list` /
`playlistItems.list` cost 1 — so the home page uses cheap `mostPopular`
calls and search is only used for user searches, related videos and the
Cooking chip. Responses are cached server-side for 5 minutes per query.

## Vidownloads

Paste a video URL, it fetches the title, thumbnail, uploader,
and available quality options, and you pick one to download.
Lives at `/vidownloads` (same downloader as before, unchanged API).

## Supported platforms (Vidownloads)

YouTube, Instagram, Facebook, X/Twitter, LinkedIn, Reddit, TikTok, Vimeo,
Dailymotion, Twitch, XHamster (including numbered `.com` and `.desi` variants),
Pornhub, Eporner, XVideos, XNXX, YouPorn,
RedGIFs, SpankBang, Tube8, YouJizz, Chaturbate, and Motherless. This list is
deliberately curated (see `SUPPORTED_DOMAINS` in `app.py`) — it isn't every
site yt-dlp can theoretically reach, and availability can vary by video,
region, or login requirements.

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

Then open http://localhost:5000 in a browser. Set `YOUTUBE_API_KEY`
first if you want MyTube to show real videos (see above).

## How it works

- `/` — landing page with two cards: MyTube and Vidownloads.
- `GET /mytube` — home: trending (`videos.list`, `mostPopular`,
  region IN) + category picks; watch history (localStorage) boosts
  watched channels and drives the "Because you watched" row.
- `GET /mytube/search?q=...` — `search.list` results.
- `GET /mytube/watch?v=...` — video details + related videos
  (`search.list` with `relatedToVideoId`), official YouTube embed.
- `GET /mytube/channel/<id>` — channel details + uploads
  (`playlistItems.list` on the uploads playlist).
- `youtube_api.py` — the YouTube Data API v3 client (stdlib `urllib`
  only), 5-minute server-side cache, India-availability filter
  (public + embeddable + not region-blocked in IN), same conventions
  as the My Music app.
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
.
├── app.py                  # Flask routes (landing, Vidownloads, MyTube) + yt-dlp calls
├── youtube_api.py          # YouTube Data API v3 client (MyTube data layer)
├── requirements.txt
├── templates/
│   ├── landing.html        # two-card landing page
│   ├── index.html          # Vidownloads page shell
│   └── mytube/             # MyTube pages (base, home, search, watch, channel, ...)
└── static/
    ├── style.css           # shared palette + landing styles
    ├── mytube.css          # MyTube layout
    ├── mytube.js           # history / subscriptions / likes / autoplay-next
    └── script.js           # Vidownloads: fetch info, render quality list, trigger download
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