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
        # formats=missing_pot: YouTube now demands a PO Token for video streams;
        # the phone cannot mint one, so yt-dlp would silently DROP every video
        # format (only audio survived). This keeps them - URLs usually still work.
        # android_vr first: it still serves the full 144p-4K ladder while the
        # plain android client is now crippled to a handful of formats.
        # web_safari: its HLS formats don't need a GVS PO Token.
        "extractor_args": {"youtube": {
            "player_client": ["android_vr", "android", "web", "web_safari"],
            "formats": ["missing_pot"],
        }},
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
    """DASH fallback options: separate video+audio joined on-device via
    Android MediaMuxer (no ffmpeg). Two mux pairs:
      mp4  = avc1/mp4 video + m4a audio  -> .mp4
      webm = vp9/webm video + opus audio -> .webm
    skip_upto: don't repeat heights already offered as single-file."""
    audios = set()
    for f in fmts:
        if f.get("vcodec") in (None, "none") and f.get("acodec") not in (None, "none"):
            ac = f.get("acodec") or ""
            if f.get("ext") == "m4a" or ac.startswith("mp4a"):
                audios.add("mp4")
            if f.get("ext") == "webm" or ac.startswith("opus"):
                audios.add("webm")

    def best_vonly(vprefixes, ext):
        # best (highest tbr) video-only format_id per height -> {height: itag}
        best = {}
        for f in fmts:
            h = f.get("height") or 0
            vc = f.get("vcodec") or ""
            if (h > skip_upto and vc.startswith(vprefixes)
                    and f.get("acodec") in (None, "none") and f.get("ext") == ext
                    and f.get("url")):
                tbr = f.get("tbr") or 0
                if h not in best or tbr > (best[h][1] or 0):
                    best[h] = (str(f.get("format_id")), tbr)
        return {h: itag for h, (itag, _) in best.items()}

    out = []
    labels = set()
    mp4cand = best_vonly(("avc", "avc1"), "mp4")
    if "mp4" in audios:
        for h in sorted(mp4cand, reverse=True)[:4]:
            out.append({"id": "__dashm__%s" % mp4cand[h],
                        "label": "%dp Video (HD)" % h})
            labels.add(h)
    if "webm" in audios:
        # YouTube reports vp9 as "vp09.00.10.08" - match both spellings
        webmcand = best_vonly(("vp9", "vp09"), "webm")
        for h in sorted(webmcand, reverse=True)[:4]:
            if h not in labels:
                out.append({"id": "__dashw__%s" % webmcand[h],
                            "label": "%dp Video (HD)" % h})
                labels.add(h)
    return out


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
        # "Audio only" sirf tab dikhao jab audio-only format waqai mojood ho -
        # warna ye option chunne par download fail hona hi hai
        has_aonly = any(f.get("vcodec") in (None, "none")
                        and f.get("acodec") not in (None, "none")
                        for f in (info.get("formats") or []))
        if has_aonly:
            formats.append({"id": "__audio__", "label": "Audio only (M4A)"})

    fmts_all = info.get("formats") or []
    n_prog = sum(1 for f in fmts_all
                 if f.get("vcodec") not in (None, "none") and f.get("acodec") not in (None, "none"))
    vcodecs = {}
    for f in fmts_all:
        if f.get("vcodec") not in (None, "none") and f.get("acodec") in (None, "none"):
            vc = (f.get("vcodec") or "").split(".")[0][:4]
            vcodecs[vc] = vcodecs.get(vc, 0) + 1
    n_aonly = sum(1 for f in fmts_all
                  if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none"))
    acodecs = {}
    for f in fmts_all:
        if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none"):
            ac = (f.get("acodec") or "").split(".")[0][:4]
            acodecs[ac] = acodecs.get(ac, 0) + 1
    dbg = "kul %d | video+audio %d | sirf-video %d%s | sirf-audio %d%s" % (
        len(fmts_all), n_prog, sum(vcodecs.values()),
        ("(%s)" % ",".join("%s:%d" % kv for kv in sorted(vcodecs.items())) if vcodecs else ""),
        n_aonly,
        ("(%s)" % ",".join("%s:%d" % kv for kv in sorted(acodecs.items())) if acodecs else ""))

    return json.dumps({
        "ok": True,
        "title": info.get("title"),
        "uploader": info.get("uploader") or "",
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "page_url": info.get("webpage_url") or url,
        "formats": formats,
        "dbg": dbg,
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


def _download_dash(base_opts, target, outdir, video_itag, cb, retry, mux="mp4"):
    def hook(d):
        if d.get("status") == "downloading":
            try:
                cb.onProgress(d.get("downloaded_bytes") or 0,
                              d.get("total_bytes") or d.get("total_bytes_estimate") or 0)
            except Exception:
                pass

    if mux == "webm":
        vfile = os.path.join(outdir, "video_part.webm")
        afile = os.path.join(outdir, "audio_part.webm")
        afmt = "ba[ext=webm]/ba[acodec^=opus]/bestaudio"
    else:
        vfile = os.path.join(outdir, "video_part.mp4")
        afile = os.path.join(outdir, "audio_part.m4a")
        afmt = "ba[ext=m4a]/ba[acodec^=mp4a]/bestaudio"
    vopts = dict(base_opts)
    # exact itag - no codec-name matching needed
    vopts.update({"format": video_itag, "outtmpl": vfile, "progress_hooks": [hook]})
    aopts = dict(base_opts)
    aopts.update({"format": afmt, "outtmpl": afile, "progress_hooks": [hook]})
    _run_download(vopts, target, retry)
    _run_download(aopts, target, retry)
    # Kotlin joins the two parts with Android MediaMuxer (no ffmpeg)
    return "mux:%s:%s|%s" % (mux, vfile, afile)


def _android_only_opts():
    """_base_opts() jaisa, lekin sirf YouTube Android client - 403 aane par
    isi se naya download link lekar dobara try karte hain."""
    o = _base_opts()
    try:
        o["extractor_args"]["youtube"]["player_client"] = ["android"]
    except Exception:
        pass
    return o


def download(url, page_url, format_id, outdir, cb):
    def hook(d):
        if d.get("status") == "downloading":
            try:
                cb.onProgress(d.get("downloaded_bytes") or 0,
                              d.get("total_bytes") or d.get("total_bytes_estimate") or 0)
            except Exception:
                pass

    target = page_url or url
    last_err = None
    # Retry loop: 403 (phone ke network/IP par YouTube ka download link
    # block) -> sirf Android client se naya link lekar dobara try karo.
    # 403 ke ilawa error foran dikhao, retry nahi.
    for attempt in range(4):
        base = _base_opts() if attempt == 0 else _android_only_opts()
        opts = dict(base)
        opts.update({
            "outtmpl": os.path.join(outdir, "%(title).80s.%(ext)s"),
            "restrictfilenames": True,
            "progress_hooks": [hook],
        })
        try:
            if format_id == "__audio__":
                opts["format"] = "bestaudio[ext=m4a]/bestaudio/best"
                _run_download(opts, target, 0)
                return "done"
            elif format_id.startswith("__dashw__"):
                video_itag = format_id.split("__")[2]
                return _download_dash(base, target, outdir, video_itag, cb, 0, mux="webm")
            elif format_id.startswith("__dashm__"):
                video_itag = format_id.split("__")[2]
                return _download_dash(base, target, outdir, video_itag, cb, 0, mux="mp4")
            else:
                opts["format"] = format_id
                _run_download(opts, target, 0)
                return "done"
        except Exception as e:
            last_err = e
            if "403" in str(e):
                continue  # naya link lekar dobara
            if _is_tiktok(target) and attempt < 3:
                time.sleep(3)
                continue
            raise
    raise last_err
