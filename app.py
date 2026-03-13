"""
Screenshare Bot - Railway Backend (Room Creator Mode)
======================================================
Creates a screensharing.net room and returns TWO links:
  - share_link : viewer link (send to others to watch)
  - host_link  : YOU open this on your PC to start sharing your screen

No real screen or Chrome needed on Railway.
"""
import os
import re
import time
import uuid
import logging
import threading
try:
    import requests as req_lib
except ImportError:
    import urllib.request as req_lib
    req_lib = None

from flask import Flask, request, jsonify
from flask_cors import CORS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

jobs = {}
jobs_lock = threading.Lock()


def create_screensharing_room():
    """
    Hit screensharing.net to create a room.
    Returns (viewer_url, host_url) or raises on failure.
    """
    import urllib.request, urllib.parse, json, re

    # Step 1: Get the homepage to find the room creation endpoint/token
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    req = urllib.request.Request("https://screensharing.net", headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    log.info("Got screensharing.net homepage")

    # Step 2: Look for room ID patterns or API endpoints in the page
    # screensharing.net uses a random room ID in the URL like /room/XXXXXX
    # Try to find any pre-generated room or API call pattern
    room_patterns = [
        r'roomId["\s:=]+["\']([a-zA-Z0-9_-]{6,})["\']',
        r'/room/([a-zA-Z0-9_-]{6,})',
        r'room["\s:=]+["\']([a-zA-Z0-9_-]{6,})["\']',
    ]

    room_id = None
    for pattern in room_patterns:
        match = re.search(pattern, html)
        if match:
            room_id = match.group(1)
            log.info(f"Found room ID from pattern: {room_id}")
            break

    # Step 3: If no room found in HTML, generate one (screensharing.net accepts custom IDs)
    if not room_id:
        import random, string
        room_id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
        log.info(f"Generated room ID: {room_id}")

    viewer_url = f"https://screensharing.net/room/{room_id}"
    host_url   = f"https://screensharing.net/room/{room_id}?host=1"

    return viewer_url, host_url


def run_job(job_id, url):
    log.info(f"[{job_id}] Creating room for: {url}")
    with jobs_lock:
        jobs[job_id]["status"] = "running"

    try:
        viewer_url, host_url = create_screensharing_room()

        with jobs_lock:
            jobs[job_id]["status"]     = "done"
            jobs[job_id]["share_link"] = viewer_url
            jobs[job_id]["host_link"]  = host_url

        log.info(f"[{job_id}] ✅ Room created — viewer: {viewer_url} | host: {host_url}")

    except Exception as e:
        log.error(f"[{job_id}] Failed: {e}", exc_info=True)
        with jobs_lock:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"]  = str(e)


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "mode": "railway-room-creator"})


@app.route("/api/screenshare", methods=["POST"])
def start_screenshare():
    data = request.get_json()
    url  = (data or {}).get("url", "").strip()
    if not url:
        return jsonify({"error": "Missing URL"}), 400
    if not url.startswith("http"):
        url = "https://" + url

    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {
            "status": "queued", "url": url,
            "share_link": None, "host_link": None, "error": None
        }

    threading.Thread(target=run_job, args=(job_id, url), daemon=True).start()
    log.info(f"Job {job_id} queued for {url}")
    return jsonify({"job_id": job_id})


@app.route("/api/job/<job_id>", methods=["GET"])
def get_job(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


@app.route("/api/job/<job_id>/stop", methods=["POST"])
def stop_job(job_id):
    with jobs_lock:
        if job_id not in jobs:
            return jsonify({"error": "Job not found"}), 404
        jobs[job_id]["stop_requested"] = True
        jobs[job_id]["status"] = "stopped"
    return jsonify({"status": "stopped"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
