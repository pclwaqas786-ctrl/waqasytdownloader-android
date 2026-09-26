"""yt-dlp helper, runs on-device via Chaquopy. No ffmpeg needed:
video = best single-file (audio+video) mp4, audio = m4a direct."""
import json
import os
import yt_dlp

ALLOWED_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be",
    "music.youtube.com",
    "instagram.com", "www.instagram.com",
    "tiktok.com", "www.tiktok.com", "vm.tiktok.com", "vt.tiktok.com",
    "facebook.com", "www.facebook.com", "m.facebook.com", "fb.watch",
    "linkedin.com", "www.linkedin.com",
    "x.com", "www.x.com", "twitter.com", "www.twitter.com",
    "vimeo.com", "www.vimeo.com",
}
MAX_DURATION = 60 * 60


def _base_opts():
    return {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
    }


def fetch_info(url):
    try:
        with yt_dlp.YoutubeDL(_base_opts()) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)[:300]})
    if (info.get("duration") or 0) > MAX_DURATION:
        return json.dumps({"ok": False, "error": "Video 60 minute se lambi hai."})

    # single-file formats only (video+audio together, no merge needed)
    cands = [f for f in (info.get("formats") or [])
             if f.get("vcodec") not in (None, "none")
             and f.get("acodec") not in (None, "none")]
    cands.sort(key=lambda f: (f.get("height") or 0), reverse=True)

    formats = []
    seen = set()
    for f in cands:
        h = f.get("height")
        if not h or h in seen:
            continue
        seen.add(h)
        formats.append({"id": f["format_id"], "label": "%dp Video" % h})
        if len(formats) >= 6:
            break
    if formats:
        formats.insert(0, {"id": formats[0]["id"], "label": "Best quality"})
    formats.append({"id": "__audio__", "label": "Audio only (M4A)"})

    return json.dumps({
        "ok": True,
        "title": info.get("title"),
        "uploader": info.get("uploader"),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "page_url": info.get("webpage_url") or url,
        "formats": formats,
    })


def download(url, page_url, format_id, outdir, cb):
    def hook(d):
        if d.get("status") == "downloading":
            try:
                cb.onProgress(d.get("downloaded_bytes") or 0,
                              d.get("total_bytes") or d.get("total_bytes_estimate") or 0)
            except Exception:
                pass

    opts = _base_opts()
    opts.update({
        "outtmpl": os.path.join(outdir, "%(title).80s.%(ext)s"),
        "restrictfilenames": True,
        "progress_hooks": [hook],
    })
    if format_id == "__audio__":
        opts["format"] = "bestaudio[ext=m4a]/bestaudio/best"
    else:
        opts["format"] = format_id
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([page_url or url])
    return "done"
