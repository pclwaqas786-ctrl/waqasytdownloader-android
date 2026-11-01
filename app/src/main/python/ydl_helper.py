"""yt-dlp helper, runs on-device via Chaquopy.
video = best single-file (audio+video) mp4 when available;
DASH fallback: best video-only + best audio joined on-device via Android
MediaMuxer (no ffmpeg needed); audio = m4a direct."""
import json
import os
import time
import yt_dlp

ALLOWED_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be",
    "music.youtube.com",
    "instagram.com", "www.instagram.com",
    "tiktok.com", "www.tiktok.com", "vm.tiktok.com", "vt.tiktok.com",
    "facebook.com", "www.facebook.com", "m.facebook.com", "fb.watch",
    "linkedin.com", "www.linkedin.com", "lnkd.in",
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
        "extractor_args": {"youtube": {"player_client": ["android", "web", "tv_samsung", "web_safari"]}},
    }


def _host_of(url):
    try:
        return (url or "").split("/")[2].lower()
    except Exception:
        return ""


def _is_tiktok(url):
    return "tiktok" in _host_of(url)


def _is_linkedin(url):
    h = _host_of(url)
    return "linkedin" in h or "lnkd.in" in h


def _extract_with_retry(ydl, url, attempts=3):
    """TikTok often refuses one request and allows the next; retry transient failures."""
    last = None
    for i in range(attempts):
        try:
            return ydl.extract_info(url, download=False)
        except Exception as e:
            last = e
            if i < attempts - 1:
                time.sleep(3)
    raise last


def _dash_options(fmts, skip_upto=0):
    """DASH fallback options: avc1/mp4 video-only heights joined on-device
    with m4a audio via Android MediaMuxer. skip_upto: don't repeat heights
    already offered as single-file."""
    has_audio = any(
        f.get("acodec") not in (None, "none")
        and f.get("vcodec") in (None, "none")
        and (f.get("ext") == "m4a" or (f.get("acodec") or "").startswith("mp4a"))
        for f in fmts)
    if not has_audio:
        return []
    heights = set()
    for f in fmts:
        h = f.get("height") or 0
        if (h > skip_upto and (f.get("vcodec") or "").startswith("avc")
                and f.get("acodec") in (None, "none") and f.get("ext") == "mp4"):
            heights.add(h)
    return [{"id": "__dash__%d" % h, "label": "%dp Video (HD)" % h}
            for h in sorted(heights, reverse=True)[:4]]


def fetch_info(url):
    try:
        with yt_dlp.YoutubeDL(_base_opts()) as ydl:
            if _is_tiktok(url):
                info = _extract_with_retry(ydl, url)
            else:
                info = ydl.extract_info(url, download=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)[:300]})
    if (info.get("duration") or 0) > MAX_DURATION:
        return json.dumps({"ok": False, "error": "Video 60 minute se lambi hai."})

    if _is_linkedin(url):
        # LinkedIn serves progressive MP4s without vcodec/acodec metadata;
        # treat them as single-file video candidates, labeled by bitrate.
        cands = [f for f in (info.get("formats") or []) if f.get("url")]
        cands.sort(key=lambda f: (f.get("tbr") or 0), reverse=True)
        formats = []
        seen = set()
        for f in cands:
            tbr = int(f.get("tbr") or 0)
            if tbr in seen:
                continue
            seen.add(tbr)
            formats.append({"id": str(f.get("format_id")),
                            "label": "Video (%dk)" % tbr if tbr else "Video"})
            if len(formats) >= 6:
                break
        if formats:
            formats.insert(0, {"id": formats[0]["id"], "label": "Best quality"})
        # LinkedIn has no separate audio streams; skip the audio-only option.
    else:
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
        # DASH fallback: separate video+audio joined on-device (YouTube is
        # deprecating single-file streams; some videos/Shorts are DASH-only)
        top_prog = max([f.get("height") or 0 for f in cands] or [0])
        formats.extend(_dash_options(info.get("formats") or [], skip_upto=top_prog))
        formats.append({"id": "__audio__", "label": "Audio only (M4A)"})

    return json.dumps({
        "ok": True,
        "title": info.get("title"),
        "uploader": info.get("uploader") or "",
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "page_url": info.get("webpage_url") or url,
        "formats": formats,
    })


def _run_download(ydl_opts, target, retry):
    if retry:
        last = None
        for i in range(3):
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([target])
                return
            except Exception as e:
                last = e
                if i < 2:
                    time.sleep(3)
        raise last
    else:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([target])


def _download_dash(base_opts, target, outdir, height, cb, retry):
    def hook(d):
        if d.get("status") == "downloading":
            try:
                cb.onProgress(d.get("downloaded_bytes") or 0,
                              d.get("total_bytes") or d.get("total_bytes_estimate") or 0)
            except Exception:
                pass

    vfile = os.path.join(outdir, "video_part.mp4")
    afile = os.path.join(outdir, "audio_part.m4a")
    vopts = dict(base_opts)
    vopts.update({
        "format": ("bv*[vcodec^=avc][ext=mp4][height<=%d]/"
                   "bv*[height<=%d][ext=mp4]/bv[height<=%d]") % (height, height, height),
        "outtmpl": vfile,
        "progress_hooks": [hook],
    })
    aopts = dict(base_opts)
    aopts.update({
        "format": "ba[ext=m4a]/ba[acodec^=mp4a]/bestaudio",
        "outtmpl": afile,
        "progress_hooks": [hook],
    })
    _run_download(vopts, target, retry)
    _run_download(aopts, target, retry)
    # Kotlin joins the two parts with Android MediaMuxer (no ffmpeg)
    return "dash:%s|%s" % (vfile, afile)


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
    target = page_url or url
    retry = _is_tiktok(target)
    if format_id == "__audio__":
        opts["format"] = "bestaudio[ext=m4a]/bestaudio/best"
        _run_download(opts, target, retry)
        return "done"
    elif format_id.startswith("__dash__"):
        height = int(format_id.split("__")[2])
        return _download_dash(_base_opts(), target, outdir, height, cb, retry)
    else:
        opts["format"] = format_id
        _run_download(opts, target, retry)
        return "done"
