"""
Screenshare Bot - Railway Backend (Direct Mode)
================================================
Since screensharing.net requires you to click Share My Screen to generate
a real room ID, this backend simply returns the screensharing.net URL
so the user can open it and start sharing manually.
"""
import os
import uuid
import logging
import threading
from flask import Flask, request, jsonify
from flask_cors import CORS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

jobs = {}
jobs_lock = threading.Lock()


def run_job(job_id, url):
    log.info(f"[{job_id}] Processing: {url}")
    with jobs_lock:
        jobs[job_id]["status"]     = "done"
        jobs[job_id]["share_link"] = "https://screensharing.net"
        jobs[job_id]["host_link"]  = "https://screensharing.net"
        jobs[job_id]["target_url"] = url
    log.info(f"[{job_id}] Done")


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "mode": "railway"})


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
            "share_link": None, "host_link": None,
            "target_url": url, "error": None
        }

    threading.Thread(target=run_job, args=(job_id, url), daemon=True).start()
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
        jobs[job_id]["status"] = "stopped"
    return jsonify({"status": "stopped"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
