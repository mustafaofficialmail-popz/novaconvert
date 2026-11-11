# NovaConvert YouTube metadata version

This replaces the local file converter with YouTube video/playlist metadata lookup. It uses the official YouTube Data API for public titles, thumbnails, channel details and playlist items. It does not download YouTube streams or bypass restrictions.

## Get an API key
1. Open Google Cloud Console.
2. Create/select a project.
3. Enable YouTube Data API v3.
4. Create an API key. Restrict it to YouTube Data API v3. Never publish the key in frontend code.

## Run locally
In the VS Code backend terminal, stop the old server with Ctrl+C. Then:
```bash
pip install -r requirements.txt
export YOUTUBE_API_KEY="PASTE_YOUR_API_KEY_HERE"
python app.py
```
Keep it running. Open a second terminal:
```bash
cd ~/Documents/video-downloader/frontend
python3 -m http.server 8000
```
Visit http://127.0.0.1:8000.

## Deploy
Push the project to GitHub. Deploy the backend on Render using backend/render.yaml, then add YOUTUBE_API_KEY in Render Environment settings. Deploy frontend/ as a Netlify site. Change API_BASE in frontend/script.js to your Render service URL and redeploy.

The YouTube Data API does not provide a general video/audio download endpoint. Use YouTube Studio's official download option for your own uploads or authorized downloadable sources for conversion.
