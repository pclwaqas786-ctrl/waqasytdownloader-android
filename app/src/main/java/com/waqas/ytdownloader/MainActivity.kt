package com.waqas.ytdownloader

import android.app.Activity
import android.content.ContentValues
import android.graphics.BitmapFactory
import android.os.Bundle
import android.provider.MediaStore
import android.view.View
import android.widget.*
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.json.JSONObject
import java.io.File
import java.net.URL

class MainActivity : Activity() {

    data class Fmt(val id: String, val label: String)

    private val allowedHosts = setOf(
        "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be",
        "music.youtube.com",
        "instagram.com", "www.instagram.com",
        "tiktok.com", "www.tiktok.com", "vm.tiktok.com", "vt.tiktok.com",
        "facebook.com", "www.facebook.com", "m.facebook.com", "fb.watch",
        "linkedin.com", "www.linkedin.com", "lnkd.in",
        "x.com", "www.x.com", "twitter.com", "www.twitter.com",
        "vimeo.com", "www.vimeo.com"
    )

    private var formats: List<Fmt> = emptyList()
    private var pageUrl: String = ""

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        if (!Python.isStarted()) Python.start(AndroidPlatform(this))

        val urlInput = findViewById<EditText>(R.id.urlInput)
        val fetchBtn = findViewById<Button>(R.id.fetchBtn)
        val dlBtn = findViewById<Button>(R.id.dlBtn)

        fetchBtn.setOnClickListener {
            val url = urlInput.text.toString().trim()
            if (!validUrl(url)) {
                toast("Ye link supported nahi hai.")
                return@setOnClickListener
            }
            setStatus("Link read ho raha hai...")
            hideResult()
            Thread { doFetch(url) }.start()
        }

        dlBtn.setOnClickListener {
            val f = formats.getOrNull(findViewById<Spinner>(R.id.formatSpinner).selectedItemPosition)
                ?: return@setOnClickListener
            val url = urlInput.text.toString().trim()
            Thread { doDownload(url, f) }.start()
        }
    }

    private fun validUrl(url: String): Boolean {
        return try {
            val host = URL(url).host.lowercase()
            url.startsWith("http") && host in allowedHosts
        } catch (_: Exception) { false }
    }

    private fun doFetch(url: String) {
        try {
            val res = Python.getInstance().getModule("ydl_helper")
                .callAttr("fetch_info", url).toString()
            val obj = JSONObject(res)
            runOnUiThread {
                if (!obj.optBoolean("ok")) {
                    setStatus("Error: " + obj.optString("error", "link read nahi hua"))
                    return@runOnUiThread
                }
                pageUrl = obj.optString("page_url", url)
                findViewById<TextView>(R.id.titleText).text = obj.optString("title")
                val meta = listOf(obj.optString("uploader"), fmtDur(obj.optLong("duration")))
                    .filter { it.isNotEmpty() }.joinToString(" • ")
                findViewById<TextView>(R.id.metaText).text = meta
                val th = obj.optString("thumbnail")
                if (th.isNotEmpty()) {
                    findViewById<ImageView>(R.id.thumb).visibility = View.VISIBLE
                    loadThumb(th)
                }
                val arr = obj.getJSONArray("formats")
                formats = (0 until arr.length()).map { i ->
                    val o = arr.getJSONObject(i)
                    Fmt(o.getString("id"), o.getString("label"))
                }
                val spinner = findViewById<Spinner>(R.id.formatSpinner)
                val adapter = ArrayAdapter(this, android.R.layout.simple_spinner_item,
                    formats.map { it.label })
                adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item)
                spinner.adapter = adapter
                spinner.visibility = View.VISIBLE
                findViewById<Button>(R.id.dlBtn).visibility = View.VISIBLE
                // Diagnostic: if no video option came back, show raw counts
                // so the real cause is visible instead of guessing.
                if (formats.size <= 1) {
                    setStatus("Formats nahi mile. " + obj.optString("dbg"))
                } else {
                    setStatus("")
                }
            }
        } catch (e: Exception) {
            runOnUiThread { setStatus("Error: ${e.message}") }
        }
    }

    private fun doDownload(url: String, f: Fmt) {
        val dlBtn = findViewById<Button>(R.id.dlBtn)
        val progress = findViewById<ProgressBar>(R.id.progress)
        runOnUiThread {
            progress.visibility = View.VISIBLE
            progress.progress = 0
            setStatus("Download ho raha hai...")
            dlBtn.isEnabled = false
        }
        try {
            val outdir = File(cacheDir, "dl").apply { mkdirs() }
            outdir.listFiles()?.forEach { it.delete() }
            val result = Python.getInstance().getModule("ydl_helper").callAttr(
                "download", url, pageUrl, f.id, outdir.absolutePath, ProgressCb(this)
            ).toString()
            val file: File = if (result.startsWith("dash:")) {
                val parts = result.removePrefix("dash:").split("|")
                runOnUiThread { setStatus("Video+audio jor raha hun...") }
                val merged = File(outdir, "video_${System.currentTimeMillis()}.mp4")
                muxVideoAudio(parts[0], parts[1], merged.absolutePath)
                merged
            } else {
                outdir.listFiles()?.firstOrNull { it.isFile }
                    ?: throw Exception("file nahi mili")
            }
            saveToDownloads(file)
            runOnUiThread { setStatus("Ho gaya! Downloads folder me dekh lo.") }
        } catch (e: Exception) {
            runOnUiThread { setStatus("Download fail: ${e.message}") }
        } finally {
            runOnUiThread {
                progress.visibility = View.GONE
                dlBtn.isEnabled = true
            }
        }
    }

    private fun saveToDownloads(file: File) {
        val mime = if (file.extension.equals("m4a", true)) "audio/mp4" else "video/mp4"
        val values = ContentValues().apply {
            put(MediaStore.Downloads.DISPLAY_NAME, file.name)
            put(MediaStore.Downloads.MIME_TYPE, mime)
            put(MediaStore.Downloads.RELATIVE_PATH, "Download/")
        }
        val uri = contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
            ?: throw Exception("Downloads me save nahi ho saka")
        contentResolver.openOutputStream(uri)?.use { out ->
            file.inputStream().use { it.copyTo(out) }
        }
    }

    private fun loadThumb(url: String) {
        Thread {
            try {
                val bmp = BitmapFactory.decodeStream(URL(url).openConnection().getInputStream())
                runOnUiThread { findViewById<ImageView>(R.id.thumb).setImageBitmap(bmp) }
            } catch (_: Exception) { }
        }.start()
    }

    private fun fmtDur(sec: Long): String {
        if (sec <= 0) return ""
        val m = sec / 60
        val s = sec % 60
        return "%d:%02d".format(m, s)
    }

    private fun hideResult() {
        findViewById<ImageView>(R.id.thumb).visibility = View.GONE
        findViewById<Spinner>(R.id.formatSpinner).visibility = View.GONE
        findViewById<Button>(R.id.dlBtn).visibility = View.GONE
        findViewById<TextView>(R.id.titleText).text = ""
        findViewById<TextView>(R.id.metaText).text = ""
    }

    private fun setStatus(t: String) {
        findViewById<TextView>(R.id.statusText).text = t
    }

    private fun toast(t: String) {
        Toast.makeText(this, t, Toast.LENGTH_SHORT).show()
    }

    class ProgressCb(private val activity: Activity) {
        fun onProgress(done: Long, total: Long) {
            if (total > 0) activity.runOnUiThread {
                activity.findViewById<ProgressBar>(R.id.progress).progress =
                    ((done * 100) / total).toInt().coerceIn(0, 100)
            }
        }
    }

    /** Join DASH video+audio parts into one MP4 using Android's built-in
     *  MediaMuxer — no ffmpeg needed. */
    private fun muxVideoAudio(videoPath: String, audioPath: String, outPath: String) {
        val muxer = android.media.MediaMuxer(
            outPath, android.media.MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
        val vEx = android.media.MediaExtractor().apply { setDataSource(videoPath) }
        val aEx = android.media.MediaExtractor().apply { setDataSource(audioPath) }
        try {
            val vTrack = findTrack(vEx, "video/")
            val aTrack = findTrack(aEx, "audio/")
            val vIdx = muxer.addTrack(vEx.getTrackFormat(vTrack))
            val aIdx = muxer.addTrack(aEx.getTrackFormat(aTrack))
            muxer.start()
            vEx.selectTrack(vTrack)
            aEx.selectTrack(aTrack)
            copyTrack(vEx, muxer, vIdx)
            copyTrack(aEx, muxer, aIdx)
        } finally {
            try { muxer.stop() } catch (_: Exception) {}
            muxer.release()
            vEx.release()
            aEx.release()
        }
    }

    private fun findTrack(ex: android.media.MediaExtractor, prefix: String): Int {
        for (i in 0 until ex.trackCount) {
            val mime = ex.getTrackFormat(i)
                .getString(android.media.MediaFormat.KEY_MIME) ?: ""
            if (mime.startsWith(prefix)) return i
        }
        throw Exception("track nahi mila: $prefix")
    }

    private fun copyTrack(
        ex: android.media.MediaExtractor,
        muxer: android.media.MediaMuxer,
        idx: Int
    ) {
        val buf = java.nio.ByteBuffer.allocate(1024 * 1024)
        val info = android.media.MediaCodec.BufferInfo()
        while (true) {
            val n = ex.readSampleData(buf, 0)
            if (n < 0) break
            info.set(0, n, ex.sampleTime, ex.sampleFlags)
            muxer.writeSampleData(idx, buf, info)
            ex.advance()
        }
    }
}
