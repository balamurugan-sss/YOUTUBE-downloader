"""
VidFetch - a self-hosted video & audio downloader.

Backend: FastAPI + yt-dlp (+ ffmpeg for merging / audio conversion).
Supports YouTube and the 1000+ sites yt-dlp understands
(Instagram, TikTok, X/Twitter, Facebook, Vimeo, Reddit, SoundCloud, ...).
"""
from __future__ import annotations

import ipaddress
import os
import re
import shutil
import socket
import threading
import time
import uuid
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import yt_dlp
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = Path(os.environ.get("VIDFETCH_DOWNLOAD_DIR", BASE_DIR / "downloads"))
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

FILE_TTL_SECONDS = int(os.environ.get("VIDFETCH_FILE_TTL", 60 * 30))  # keep files 30 min
MAX_CONCURRENT_JOBS = int(os.environ.get("VIDFETCH_MAX_JOBS", 4))
MAX_FILESIZE = os.environ.get("VIDFETCH_MAX_FILESIZE")  # e.g. "2G", optional
COOKIES_FILE = os.environ.get("VIDFETCH_COOKIES")  # optional cookies.txt for age/region gated content

VIDEO_HEIGHTS = [2160, 1440, 1080, 720, 480, 360, 240, 144]
AUDIO_FORMATS = {"mp3", "m4a", "opus", "wav"}

app = FastAPI(title="VidFetch", docs_url="/api/docs", redoc_url=None)

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _validate_url(url: str) -> str:
    """Accept only public http(s) URLs (basic SSRF protection)."""
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(400, "Please paste a valid http(s) link.")
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        # DNS may be handled by an outbound proxy; let yt-dlp try.
        return url
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise HTTPException(400, "That address is not allowed.")
    return url


def _base_opts() -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 5,
        "fragment_retries": 5,
        "concurrent_fragment_downloads": 4,
        "restrictfilenames": True,
    }
    if COOKIES_FILE and Path(COOKIES_FILE).is_file():
        opts["cookiefile"] = COOKIES_FILE
    if shutil.which("ffmpeg") is None:
        opts["prefer_ffmpeg"] = False
    return opts


def _clean_error(exc: Exception) -> str:
    msg = re.sub(r"\x1b\[[0-9;]*m", "", str(exc))
    msg = msg.replace("ERROR: ", "")
    if "Unsupported URL" in msg:
        return "This link is not supported. Try a direct video/post link."
    if "Private video" in msg or "login" in msg.lower() or "sign in" in msg.lower():
        return "This video needs a login (private, age-restricted or members-only)."
    if "Video unavailable" in msg:
        return "This video is unavailable."
    return msg[:300]


def _fmt_size(n: float | None) -> str | None:
    if not n:
        return None
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _platform_name(info: dict) -> str:
    key = (info.get("extractor_key") or info.get("extractor") or "web").lower()
    names = {
        "youtube": "YouTube", "youtubetab": "YouTube", "instagram": "Instagram",
        "tiktok": "TikTok", "twitter": "X / Twitter", "facebook": "Facebook",
        "vimeo": "Vimeo", "reddit": "Reddit", "soundcloud": "SoundCloud",
        "dailymotion": "Dailymotion", "twitch": "Twitch", "pinterest": "Pinterest",
        "linkedin": "LinkedIn", "threads": "Threads", "snapchat": "Snapchat",
        "bilibili": "Bilibili", "generic": "Web",
    }
    for k, v in names.items():
        if key.startswith(k):
            return v
    return info.get("extractor_key") or "Web"


# --------------------------------------------------------------------------- #
# Info endpoint
# --------------------------------------------------------------------------- #

class InfoRequest(BaseModel):
    url: str = Field(..., max_length=2048)


@app.post("/api/info")
def get_info(req: InfoRequest):
    url = _validate_url(req.url)
    try:
        with yt_dlp.YoutubeDL(_base_opts()) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        raise HTTPException(422, _clean_error(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, _clean_error(exc))

    if info.get("_type") == "playlist" and info.get("entries"):
        info = next((e for e in info["entries"] if e), info)

    formats = info.get("formats") or []
    duration = info.get("duration") or 0

    # best audio size (used to estimate merged video size)
    audio_only = [f for f in formats if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")]
    best_audio = max(audio_only, key=lambda f: f.get("abr") or f.get("tbr") or 0, default=None)
    audio_size = (best_audio or {}).get("filesize") or (best_audio or {}).get("filesize_approx") or 0

    # Collect available video heights
    video_options = []
    seen = set()
    video_fmts = [f for f in formats if f.get("height") and f.get("vcodec") != "none"]
    heights_available = sorted({f["height"] for f in video_fmts}, reverse=True)
    for h in heights_available:
        candidates = [f for f in video_fmts if f["height"] == h]
        # label by the short side so vertical videos (Shorts/Reels/TikTok) read naturally
        short_side = min(h, max((f.get("width") or h) for f in candidates))
        bucket = next((v for v in VIDEO_HEIGHTS if short_side >= v * 0.9), None)
        if bucket is None or bucket in seen:
            continue
        seen.add(bucket)
        best = max(candidates, key=lambda f: (f.get("filesize") or f.get("filesize_approx") or 0))
        size = (best.get("filesize") or best.get("filesize_approx") or 0)
        if size and best.get("acodec") == "none":
            size += audio_size
        fps = best.get("fps")
        video_options.append({
            "quality": h,  # real stream height, used as the download filter
            "label": {2160: "4K", 1440: "2K"}.get(bucket, f"{bucket}p"),
            "badge": "Ultra HD" if bucket >= 2160 else "QHD" if bucket >= 1440 else "Full HD" if bucket >= 1080 else "HD" if bucket >= 720 else "SD",
            "fps": int(fps) if fps and fps > 30 else None,
            "size": _fmt_size(size),
        })

    # Sites that only expose a single combined format with no height info
    if not video_options and any(f.get("vcodec") != "none" for f in (formats or [info])):
        video_options.append({"quality": 0, "label": "Best", "badge": "Original", "fps": None,
                                  "size": _fmt_size(info.get("filesize") or info.get("filesize_approx"))})

    # acodec None means "unknown" (common for direct links) - assume audio may exist
    has_audio = any(f.get("acodec") != "none" for f in (formats or [info]))
    audio_options = []
    if has_audio:
        for fmt, kbps, label in (("mp3", 320, "MP3 · 320 kbps"), ("mp3", 192, "MP3 · 192 kbps"),
                                 ("mp3", 128, "MP3 · 128 kbps"), ("m4a", 0, "M4A · Original"),
                                 ("opus", 0, "OPUS · Original"), ("wav", 0, "WAV · Lossless")):
            est = (kbps * 1000 / 8 * duration) if (kbps and duration) else (audio_size if fmt in ("m4a", "opus") else None)
            audio_options.append({"format": fmt, "bitrate": kbps, "label": label, "size": _fmt_size(est)})

    return {
        "id": info.get("id"),
        "url": info.get("webpage_url") or url,
        "title": info.get("title") or "Untitled",
        "uploader": info.get("uploader") or info.get("channel") or info.get("uploader_id"),
        "thumbnail": info.get("thumbnail"),
        "duration": duration,
        "view_count": info.get("view_count"),
        "platform": _platform_name(info),
        "video": video_options,
        "audio": audio_options,
    }


# --------------------------------------------------------------------------- #
# Download jobs
# --------------------------------------------------------------------------- #

class DownloadRequest(BaseModel):
    url: str = Field(..., max_length=2048)
    kind: Literal["video", "audio"]
    quality: int = 0          # video height (0 = best)
    format: str = "mp3"       # audio format
    bitrate: int = 0          # audio kbps (0 = original/best)


JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
SEMAPHORE = threading.Semaphore(MAX_CONCURRENT_JOBS)


def _job_update(job_id: str, **kw):
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(kw)


def _run_job(job_id: str, req: DownloadRequest):
    job_dir = DOWNLOAD_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    def hook(d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            pct = (done / total * 100) if total else 0
            # two streams (video + audio) are downloaded for merged formats
            stage = JOBS.get(job_id, {}).get("stream", 0)
            streams = JOBS.get(job_id, {}).get("streams", 1)
            overall = (stage * 100 + pct) / streams
            _job_update(job_id, status="downloading", progress=round(min(overall, 99), 1),
                        speed=d.get("speed"), eta=d.get("eta"),
                        downloaded=_fmt_size(done), total=_fmt_size(total))
        elif d["status"] == "finished":
            with JOBS_LOCK:
                j = JOBS.get(job_id)
                if j:
                    j["stream"] = min(j.get("stream", 0) + 1, j.get("streams", 1))

    def pp_hook(d):
        if d["status"] == "started":
            _job_update(job_id, status="processing", progress=99)

    opts = _base_opts()
    opts.update({
        "outtmpl": str(job_dir / "%(title).150B [%(id)s].%(ext)s"),
        "progress_hooks": [hook],
        "postprocessor_hooks": [pp_hook],
    })
    if MAX_FILESIZE:
        opts["max_filesize"] = yt_dlp.parse_filesize(MAX_FILESIZE)

    if req.kind == "video":
        h = req.quality
        if h:
            fmt = (f"bv*[height<={h}][ext=mp4]+ba[ext=m4a]/bv*[height<={h}]+ba/"
                   f"b[height<={h}]/bv*+ba/b")
        else:
            fmt = "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b"
        opts.update({"format": fmt, "merge_output_format": "mp4"})
        streams = 2
    else:
        fmt = req.format if req.format in AUDIO_FORMATS else "mp3"
        opts.update({
            "format": "ba/b",
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": fmt,
                 "preferredquality": str(req.bitrate) if req.bitrate else "0"},
                {"key": "FFmpegMetadata"},
            ],
        })
        streams = 1

    _job_update(job_id, streams=streams, stream=0)

    with SEMAPHORE:
        _job_update(job_id, status="starting", progress=0)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.extract_info(req.url, download=True)
            files = [p for p in job_dir.iterdir() if p.is_file() and not p.name.endswith((".part", ".ytdl"))]
            if not files:
                raise RuntimeError("Download finished but no file was produced.")
            final = max(files, key=lambda p: p.stat().st_size)
            _job_update(job_id, status="done", progress=100, filename=final.name,
                        path=str(final), size=_fmt_size(final.stat().st_size))
        except Exception as exc:  # noqa: BLE001
            _job_update(job_id, status="error", error=_clean_error(exc))
            shutil.rmtree(job_dir, ignore_errors=True)


@app.post("/api/download")
def start_download(req: DownloadRequest):
    req.url = _validate_url(req.url)
    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {"id": job_id, "status": "queued", "progress": 0, "created": time.time(), "kind": req.kind}
    threading.Thread(target=_run_job, args=(job_id, req), daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/progress/{job_id}")
def progress(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        public = {k: v for k, v in job.items() if k not in ("path",)}
    return JSONResponse(public)


@app.get("/api/file/{job_id}")
def get_file(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or job.get("status") != "done":
        raise HTTPException(404, "File not ready")
    path = Path(job["path"])
    if not path.is_file():
        raise HTTPException(410, "File expired, please download again")
    return FileResponse(path, filename=job["filename"], media_type="application/octet-stream")


# --------------------------------------------------------------------------- #
# Cleanup of old files
# --------------------------------------------------------------------------- #

def _cleanup_loop():
    while True:
        now = time.time()
        with JOBS_LOCK:
            expired = [jid for jid, j in JOBS.items() if now - j["created"] > FILE_TTL_SECONDS
                       and j["status"] in ("done", "error")]
            for jid in expired:
                JOBS.pop(jid, None)
        for jid in expired:
            shutil.rmtree(DOWNLOAD_DIR / jid, ignore_errors=True)
        # orphaned dirs from previous runs
        for d in DOWNLOAD_DIR.iterdir():
            if d.is_dir() and d.name not in JOBS and now - d.stat().st_mtime > FILE_TTL_SECONDS:
                shutil.rmtree(d, ignore_errors=True)
        time.sleep(60)


@app.on_event("startup")
def _startup():
    threading.Thread(target=_cleanup_loop, daemon=True).start()


@app.get("/api/health")
def health():
    return {"ok": True, "ffmpeg": shutil.which("ffmpeg") is not None, "yt_dlp": yt_dlp.version.__version__}


app.mount("/", StaticFiles(directory=BASE_DIR / "static", html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", 8000)))
