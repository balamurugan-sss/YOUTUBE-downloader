# VidFetch — Video & Audio Downloader

A self-hosted downloader with a clean, animated UI. Paste a link, pick a quality, done.

- **YouTube** (videos, Shorts), **Instagram**, **TikTok**, **X/Twitter**, **Facebook**, **Vimeo**, **Reddit**, **SoundCloud** and [1000+ other sites](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md)
- **Video:** MP4 from 144p up to 4K, with audio merged in automatically
- **Audio:** MP3 (320/192/128 kbps), M4A, OPUS or WAV
- Live progress bar (speed + ETA), multiple downloads at once, auto-save when finished
- Paste-to-start, platform detection, dark/light theme, mobile friendly
- No accounts, no limits, no watermark; files are auto-deleted from the server after 30 minutes

Built with **FastAPI** + **yt-dlp** + **ffmpeg**, and a plain HTML/CSS/JS frontend (no build step).

## Run locally

Requirements:
- Python 3.10+
- [ffmpeg](https://ffmpeg.org/download.html) on your PATH
- A JavaScript runtime for YouTube: [Deno](https://deno.com) (recommended), Node.js or Bun

Install Deno: `winget install DenoLand.Deno` (Windows), `brew install deno` (macOS) or `curl -fsSL https://deno.land/install.sh | sh` (Linux).

```bash
pip install -r requirements.txt
python app.py
```

Open http://localhost:8000

## Run with Docker

```bash
docker build -t vidfetch .
docker run -p 8000:8000 vidfetch
```

## Deploy online

Any host that runs a Docker container or a Python web service works (Render, Railway, Fly.io, a VPS, ...).
Use the Dockerfile, expose port `8000` (or set `PORT`).

> **Tip:** YouTube often blocks datacenter IPs. If YouTube links fail on your server but work on your PC,
> provide a `cookies.txt` (see below) or run on a residential/home connection.

## Configuration (environment variables)

| Variable | Default | Description |
|---|---|---|
| `PORT` / `HOST` | `8000` / `0.0.0.0` | Where the server listens |
| `VIDFETCH_DOWNLOAD_DIR` | `./downloads` | Temporary storage for finished files |
| `VIDFETCH_FILE_TTL` | `1800` | Seconds before a finished file is deleted |
| `VIDFETCH_MAX_JOBS` | `4` | Downloads processed in parallel (others wait in queue) |
| `VIDFETCH_MAX_FILESIZE` | unset | Optional cap, e.g. `2G` |
| `VIDFETCH_COOKIES` | unset | Path to a Netscape `cookies.txt` for age-restricted / login-only videos |

## Troubleshooting

Open http://localhost:8000/api/health — it should show `ffmpeg: true` and at least one entry in `js_runtimes`.
The terminal running `python app.py` logs the full error for every failed link.

| Message | Fix |
|---|---|
| *The download server isn't running here* | You opened `index.html` directly or with Live Server. Run `python app.py` and open http://localhost:8000 |
| *YouTube needs a JavaScript runtime* | Install Deno (above), then restart `python app.py` |
| *YouTube is blocking this server as a bot* | Common on cloud servers. Run locally, or export a `cookies.txt` from a logged-in browser and set `VIDFETCH_COOKIES` |
| Anything else on YouTube | `pip install -U "yt-dlp[default]"` — YouTube changes often |

## Keep it working

Sites change often. When a site stops working, update yt-dlp:

```bash
pip install -U "yt-dlp[default]"
```

(The Docker image updates yt-dlp automatically on each start.)

## API

| Method | Path | Body / result |
|---|---|---|
| `POST` | `/api/info` | `{ "url": "..." }` → title, thumbnail, video qualities, audio formats |
| `POST` | `/api/download` | `{ "url", "kind": "video"\|"audio", "quality": 1080, "format": "mp3", "bitrate": 320 }` → `{ job_id }` |
| `GET` | `/api/progress/{job_id}` | status, progress %, speed, ETA |
| `GET` | `/api/file/{job_id}` | the finished file |

Interactive docs at `/api/docs`.

## Legal

Only download content you own or have permission to download, and respect each platform's Terms of Service
and the copyright laws where you live. DRM-protected content (Netflix, Prime Video, paid courses, etc.) is not supported.
