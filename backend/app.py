import ipaddress
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
MAX_DOWNLOAD_BYTES = 1_500 * 1024 * 1024
REQUEST_TIMEOUT = (15, 60)
ALLOWED_MEDIA_TYPES = {
    "video/mp4", "video/webm", "video/quicktime", "video/x-matroska",
    "video/x-msvideo", "audio/mpeg", "audio/mp3", "audio/mp4",
    "audio/aac", "audio/wav", "audio/x-wav", "audio/ogg",
    "application/octet-stream",
}
ALLOWED_EXTENSIONS = {
    ".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi",
    ".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus",
}
JOBS = {}
JOBS_LOCK = threading.Lock()


def is_public_host(hostname):
    if not hostname:
        return False
    hostname = hostname.rstrip(".").lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        return False
    try:
        addresses = [ipaddress.ip_address(hostname)]
    except ValueError:
        try:
            records = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
            addresses = list({ipaddress.ip_address(record[4][0]) for record in records})
        except (socket.gaierror, UnicodeError, ValueError):
            return False
    return bool(addresses) and all(
        address.is_global and not (
            address.is_private or address.is_loopback or address.is_link_local
            or address.is_multicast or address.is_reserved or address.is_unspecified
        )
        for address in addresses
    )


def validate_media_url(url):
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False, "Use a public HTTP or HTTPS media URL."
        if parsed.username or parsed.password:
            return False, "URLs containing embedded credentials are not accepted."
        if not parsed.hostname or not is_public_host(parsed.hostname):
            return False, "This URL host is not a permitted public internet address."
        if parsed.port and parsed.port not in {80, 443}:
            return False, "Only standard HTTP and HTTPS ports are supported."
        return True, ""
    except ValueError:
        return False, "The media URL is invalid."


def valid_youtube_url(url):
    try:
        parsed = urlparse(url)
        return (
            parsed.scheme in {"http", "https"}
            and (parsed.hostname or "").lower() in {
                "youtube.com", "www.youtube.com", "m.youtube.com",
                "music.youtube.com", "youtu.be"
            }
            and not parsed.username and not parsed.password
        )
    except Exception:
        return False


def youtube_api(endpoint, params):
    key = os.getenv("YOUTUBE_API_KEY")
    if not key:
        raise RuntimeError("YouTube metadata is not configured. Set YOUTUBE_API_KEY in Render.")
    query = dict(params)
    query["key"] = key
    response = requests.get(
        f"{YOUTUBE_API_BASE}/{endpoint}", params=query, timeout=REQUEST_TIMEOUT
    )
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code != 200:
        raise ValueError(data.get("error", {}).get("message", "YouTube API request failed."))
    return data


@app.get("/")
def home():
    return jsonify(status="ok", service="NovaConvert metadata and authorized media downloader")


@app.get("/api/health")
def health():
    return jsonify(status="ok", ffmpeg=shutil.which("ffmpeg") is not None)


@app.get("/api/video-info")
def video_info():
    url = request.args.get("url", "").strip()
    if not valid_youtube_url(url):
        return jsonify(error="Enter a valid YouTube video or playlist URL."), 400
    try:
        playlist_match = re.search(r"[?&]list=([\w-]+)", url)
        if playlist_match:
            playlist_id = playlist_match.group(1)
            playlist_data = youtube_api("playlists", {
                "part": "snippet,contentDetails", "id": playlist_id, "maxResults": 1
            })
            if not playlist_data.get("items"):
                return jsonify(error="Playlist not found or not public."), 404
            playlist = playlist_data["items"][0]
            snippet = playlist["snippet"]
            items_data = youtube_api("playlistItems", {
                "part": "snippet", "playlistId": playlist_id, "maxResults": 50
            })
            videos = []
            for item in items_data.get("items", []):
                video_snippet = item.get("snippet", {})
                thumbs = video_snippet.get("thumbnails", {})
                resource = video_snippet.get("resourceId", {})
                videos.append({
                    "videoId": resource.get("videoId", ""),
                    "title": video_snippet.get("title", "Untitled"),
                    "thumbnail": (thumbs.get("medium") or thumbs.get("default") or {}).get("url", ""),
                })
            return jsonify(
                type="playlist", title=snippet.get("title", "Playlist"),
                itemCount=playlist.get("contentDetails", {}).get("itemCount", len(videos)),
                items=videos,
            )

        match = re.search(r"(?:[?&]v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})", url)
        if not match:
            return jsonify(error="Couldn't find a video ID in that link."), 400
        video_id = match.group(1)
        data = youtube_api("videos", {"part": "snippet,contentDetails", "id": video_id})
        if not data.get("items"):
            return jsonify(error="Video not found or not public."), 404
        video = data["items"][0]
        snippet = video["snippet"]
        thumbs = snippet.get("thumbnails", {})
        return jsonify(
            type="video", title=snippet.get("title", "Video"),
            channel=snippet.get("channelTitle", ""),
            publishedAt=snippet.get("publishedAt", "")[:10],
            duration=video.get("contentDetails", {}).get("duration", ""),
            thumbnail=(thumbs.get("high") or thumbs.get("medium") or {}).get("url", ""),
            url=f"https://www.youtube.com/watch?v={video_id}",
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


def create_job(temp_dir):
    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "queued", "progress": 0, "error": None,
            "file_path": None, "filename": None, "temp_dir": temp_dir,
            "created_at": time.time(),
        }
    return job_id


def download_public_media(job_id, url, destination):
    current_url = url
    session = requests.Session()
    session.headers.update({"User-Agent": "NovaConvert/1.0"})
    response = None
    try:
        for _ in range(6):
            valid, message = validate_media_url(current_url)
            if not valid:
                raise RuntimeError(message)
            response = session.get(
                current_url, stream=True, allow_redirects=False, timeout=REQUEST_TIMEOUT
            )
            if response.is_redirect or response.is_permanent_redirect:
                location = response.headers.get("Location")
                response.close()
                if not location:
                    raise RuntimeError("The media server returned an invalid redirect.")
                current_url = urljoin(current_url, location)
                continue
            break
        else:
            raise RuntimeError("Too many redirects from the media server.")

        if not 200 <= response.status_code < 300:
            raise RuntimeError(f"The media server returned HTTP {response.status_code}.")
        content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if content_type in {"text/html", "application/json", "text/plain"}:
            raise RuntimeError("That URL returned a webpage, not a direct media file.")
        if content_type and content_type not in ALLOWED_MEDIA_TYPES:
            raise RuntimeError(f"Unsupported media content type: {content_type}")

        length = response.headers.get("Content-Length")
        if length and length.isdigit() and int(length) > MAX_DOWNLOAD_BYTES:
            raise RuntimeError("The source file exceeds the 1.5 GB limit.")
        total = int(length) if length and length.isdigit() else None
        downloaded = 0
        with open(destination, "wb") as output:
            for chunk in response.iter_content(chunk_size=256 * 1024):
                if not chunk:
                    continue
                downloaded += len(chunk)
                if downloaded > MAX_DOWNLOAD_BYTES:
                    raise RuntimeError("The source file exceeded the 1.5 GB limit.")
                output.write(chunk)
                percent = min(70, int(downloaded * 70 / total)) if total else min(65, 5 + downloaded // (1024 * 1024))
                update_job(job_id, status="downloading", progress=percent)
        return content_type
    finally:
        if response is not None:
            response.close()
        session.close()


def convert_with_ffmpeg(input_path, output_path, media_format, quality):
    if not shutil.which("ffmpeg"):
        raise RuntimeError("FFmpeg is not installed on the backend.")
    if media_format == "mp3":
        command = [
            "ffmpeg", "-y", "-i", input_path, "-vn", "-codec:a", "libmp3lame",
            "-b:a", f"{quality}k", "-id3v2_version", "3", output_path,
        ]
    else:
        command = [
            "ffmpeg", "-y", "-i", input_path, "-map", "0:v:0?", "-map", "0:a:0?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", output_path,
        ]
    result = subprocess.run(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, timeout=3600,
    )
    if result.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError((result.stderr or "FFmpeg could not process this media file.")[-1200:])


def direct_download_worker(job_id, url, media_format, quality, temp_dir):
    source_path = os.path.join(temp_dir, "source.media")
    output_path = os.path.join(temp_dir, f"NovaConvert.{media_format}")
    try:
        update_job(job_id, status="starting", progress=1)
        content_type = download_public_media(job_id, url, source_path)
        extension = Path(urlparse(url).path).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS and content_type not in ALLOWED_MEDIA_TYPES:
            raise RuntimeError("The URL does not appear to point to a supported media file.")
        update_job(job_id, status="processing", progress=75)
        convert_with_ffmpeg(source_path, output_path, media_format, quality)
        update_job(
            job_id, status="complete", progress=100,
            file_path=output_path, filename=f"NovaConvert.{media_format}",
        )
    except subprocess.TimeoutExpired:
        shutil.rmtree(temp_dir, ignore_errors=True)
        update_job(job_id, status="error", progress=0, error="Media conversion timed out.")
    except Exception as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        update_job(job_id, status="error", progress=0, error=str(exc))


@app.post("/api/direct-download")
def start_direct_download():
    data = request.get_json(silent=True) or {}
    url = str(data.get("url", "")).strip()
    media_format = str(data.get("format", "mp4")).lower()
    quality = str(data.get("quality", "192"))
    valid, message = validate_media_url(url)
    if not valid:
        return jsonify(error=message), 400
    if media_format not in {"mp4", "mp3"}:
        return jsonify(error="Choose MP4 or MP3."), 400
    if media_format == "mp3" and quality not in {"96", "128", "192", "256", "320"}:
        return jsonify(error="Choose a supported MP3 bitrate."), 400
    if media_format == "mp4":
        quality = "source"

    temp_dir = tempfile.mkdtemp(prefix="novaconvert_direct_")
    job_id = create_job(temp_dir)
    threading.Thread(
        target=direct_download_worker,
        args=(job_id, url, media_format, quality, temp_dir),
        daemon=True,
    ).start()
    return jsonify(job_id=job_id), 202


@app.get("/api/download-status/<job_id>")
def download_status(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify(error="Download job not found or expired."), 404
        return jsonify(
            status=job["status"], progress=job["progress"],
            error=job["error"], filename=job["filename"],
        )


@app.get("/api/download-file/<job_id>")
def download_file(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify(error="Download job not found or expired."), 404
        if job["status"] != "complete" or not job["file_path"]:
            return jsonify(error="Your file is not ready yet."), 409
        file_path, filename, temp_dir = job["file_path"], job["filename"], job["temp_dir"]

    response = send_file(file_path, as_attachment=True, download_name=filename)

    def cleanup():
        shutil.rmtree(temp_dir, ignore_errors=True)
        with JOBS_LOCK:
            JOBS.pop(job_id, None)

    response.call_on_close(cleanup)
    return response


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=False)
