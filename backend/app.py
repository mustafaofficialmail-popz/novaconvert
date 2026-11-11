import os
import re
import glob
import uuid
import shutil
import tempfile
import threading
import requests
import yt_dlp

from urllib.parse import urlparse
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"

# In-memory jobs are suitable for a single-process local development server.
# For a multi-worker production deployment, use Redis/Celery or another shared job store.
JOBS = {}
JOBS_LOCK = threading.Lock()


def valid_youtube_url(url):
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        return (
            parsed.scheme in ("http", "https")
            and (
                host == "youtube.com"
                or host.endswith(".youtube.com")
                or host == "youtu.be"
                or host.endswith(".youtu.be")
            )
        )
    except Exception:
        return False


def youtube_api(endpoint, params):
    key = os.getenv("YOUTUBE_API_KEY")
    if not key:
        raise RuntimeError("Backend API key missing. Set YOUTUBE_API_KEY first.")

    params = dict(params)
    params["key"] = key
    response = requests.get(
        f"{YOUTUBE_API_BASE}/{endpoint}",
        params=params,
        timeout=20
    )
    data = response.json()

    if response.status_code != 200:
        message = data.get("error", {}).get("message", "YouTube API error")
        raise ValueError(message)

    return data


@app.get("/")
def home():
    return jsonify(
        status="ok",
        service="NovaConvert YouTube metadata and download API"
    )


@app.get("/api/video-info")
def video_info():
    url = request.args.get("url", "").strip()

    if not valid_youtube_url(url):
        return jsonify(error="Enter a valid YouTube URL."), 400

    try:
        playlist_match = re.search(r"[?&]list=([\w-]+)", url)

        if playlist_match:
            playlist_id = playlist_match.group(1)
            playlist_data = youtube_api("playlists", {
                "part": "snippet,contentDetails",
                "id": playlist_id,
                "maxResults": 1
            })

            if not playlist_data.get("items"):
                return jsonify(error="Playlist not found or not public."), 404

            playlist = playlist_data["items"][0]
            snippet = playlist["snippet"]

            items_data = youtube_api("playlistItems", {
                "part": "snippet",
                "playlistId": playlist_id,
                "maxResults": 50
            })

            videos = []
            for item in items_data.get("items", []):
                video_snippet = item["snippet"]
                thumbnails = video_snippet.get("thumbnails", {})
                videos.append({
                    "videoId": video_snippet.get("resourceId", {}).get("videoId", ""),
                    "title": video_snippet.get("title", "Untitled"),
                    "thumbnail": (
                        thumbnails.get("medium")
                        or thumbnails.get("default")
                        or {}
                    ).get("url", "")
                })

            return jsonify(
                type="playlist",
                title=snippet.get("title", "Playlist"),
                itemCount=playlist.get("contentDetails", {}).get(
                    "itemCount", len(videos)
                ),
                items=videos
            )

        video_match = re.search(
            r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})",
            url
        )
        if not video_match:
            return jsonify(error="Couldn't find a video ID in that link."), 400

        video_id = video_match.group(1)
        data = youtube_api("videos", {
            "part": "snippet,contentDetails",
            "id": video_id
        })

        if not data.get("items"):
            return jsonify(error="Video not found or not public."), 404

        video = data["items"][0]
        snippet = video["snippet"]
        thumbnails = snippet.get("thumbnails", {})

        return jsonify(
            type="video",
            title=snippet.get("title", "Video"),
            channel=snippet.get("channelTitle", ""),
            publishedAt=snippet.get("publishedAt", "")[:10],
            duration=video.get("contentDetails", {}).get("duration", ""),
            thumbnail=(
                thumbnails.get("high")
                or thumbnails.get("medium")
                or {}
            ).get("url", ""),
            url=f"https://www.youtube.com/watch?v={video_id}"
        )

    except RuntimeError as exc:
        return jsonify(error=str(exc)), 503
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except requests.RequestException:
        return jsonify(error="Could not contact YouTube. Try again."), 502


def update_job(job_id, **changes):
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(changes)


def download_worker(job_id, url, media_format, quality, temp_dir):
    output_template = os.path.join(temp_dir, "%(title).180B.%(ext)s")

    options = {
        "outtmpl": output_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [],
        "retries": 5,
        "fragment_retries": 5,
        "continuedl": True,
    }

    def progress_hook(data):
        status = data.get("status")

        if status == "downloading":
            downloaded = data.get("downloaded_bytes", 0)
            total = data.get("total_bytes") or data.get("total_bytes_estimate")

            if total:
                percent = min(99, int(downloaded * 100 / total))
                update_job(
                    job_id,
                    status="downloading",
                    progress=percent
                )

        elif status == "finished":
            update_job(
                job_id,
                status="processing",
                progress=99
            )

    options["progress_hooks"] = [progress_hook]

    if media_format == "mp3":
        options.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": quality
            }]
        })
    else:
        # Prefer the requested maximum height. If unavailable, yt-dlp selects
        # the best available format at or below that height.
        height = int(quality)
        options.update({
            "format": (
                f"bestvideo[height<={height}]+bestaudio/"
                f"best[height<={height}]"
            ),
            "merge_output_format": "mp4"
        })

    try:
        update_job(job_id, status="starting", progress=0)

        with yt_dlp.YoutubeDL(options) as downloader:
            downloader.download([url])

        files = [
            path for path in glob.glob(os.path.join(temp_dir, "*"))
            if os.path.isfile(path)
        ]

        if media_format == "mp3":
            files = [path for path in files if path.lower().endswith(".mp3")]
        else:
            files = [
                path for path in files
                if path.lower().endswith((".mp4", ".mkv", ".webm"))
            ]

        if not files:
            raise RuntimeError(
                "The downloaded file was not found. Check that FFmpeg is installed."
            )

        file_path = max(files, key=os.path.getsize)

        update_job(
            job_id,
            status="complete",
            progress=100,
            file_path=file_path,
            filename=os.path.basename(file_path)
        )

    except Exception as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        message = str(exc)

        if isinstance(exc, yt_dlp.utils.DownloadError):
            message = (
                "Download failed. The video may be unavailable, restricted, "
                "or not downloadable with the selected quality."
            )

        update_job(
            job_id,
            status="error",
            progress=0,
            error=message
        )


@app.post("/api/download")
def start_download():
    data = request.get_json(silent=True) or {}
    url = str(data.get("url", "")).strip()
    media_format = str(data.get("format", "mp4")).lower()
    quality = str(data.get("quality", "360"))

    if not valid_youtube_url(url):
        return jsonify(error="Enter a valid YouTube URL."), 400

    if media_format not in ("mp4", "mp3"):
        return jsonify(error="Choose MP4 or MP3."), 400

    if media_format == "mp4" and quality not in {
        "144", "240", "360", "480", "720", "1080"
    }:
        return jsonify(error="Choose a supported MP4 resolution."), 400

    if media_format == "mp3" and quality not in {
        "96", "128", "192", "256", "320"
    }:
        return jsonify(error="Choose a supported MP3 bitrate."), 400

    job_id = uuid.uuid4().hex
    temp_dir = tempfile.mkdtemp(prefix="novaconvert_")

    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "queued",
            "progress": 0,
            "error": None,
            "file_path": None,
            "filename": None,
            "temp_dir": temp_dir
        }

    thread = threading.Thread(
        target=download_worker,
        args=(job_id, url, media_format, quality, temp_dir),
        daemon=True
    )
    thread.start()

    return jsonify(job_id=job_id), 202


@app.get("/api/download-status/<job_id>")
def download_status(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify(error="Download job not found or expired."), 404

        return jsonify(
            status=job["status"],
            progress=job["progress"],
            error=job["error"],
            filename=job["filename"]
        )


@app.get("/api/download-file/<job_id>")
def download_file(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)

        if not job:
            return jsonify(error="Download job not found or expired."), 404

        if job["status"] != "complete" or not job["file_path"]:
            return jsonify(error="Your file is not ready yet."), 409

        file_path = job["file_path"]
        filename = job["filename"]
        temp_dir = job["temp_dir"]

    response = send_file(
        file_path,
        as_attachment=True,
        download_name=filename
    )

    def cleanup():
        shutil.rmtree(temp_dir, ignore_errors=True)
        with JOBS_LOCK:
            JOBS.pop(job_id, None)

    response.call_on_close(cleanup)
    return response


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", 5000)),
        debug=True,
        use_reloader=False
    )
