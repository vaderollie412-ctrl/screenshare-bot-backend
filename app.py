"""
Screenshare Bot - Railway Backend (Working Version)
=====================================================
Uses Xvfb virtual display + Chrome to automate screensharing.net.
This is the version that was working before manual-room changes.
"""
import os
import time
import uuid
import logging
import threading
import subprocess
from flask import Flask, request, jsonify
from flask_cors import CORS
from selenium import webdriver
from selenium.webdriver.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

jobs = {}
jobs_lock = threading.Lock()
drivers = {}
drivers_lock = threading.Lock()

DISPLAY = ":99"

def start_xvfb():
    try:
        subprocess.Popen(
            ["Xvfb", DISPLAY, "-screen", "0", "1280x900x24", "-ac"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        time.sleep(2)
        os.environ["DISPLAY"] = DISPLAY
        log.info(f"Xvfb started on {DISPLAY}")
    except Exception as e:
        log.warning(f"Xvfb failed: {e}")

start_xvfb()


def make_driver():
    options = Options()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,900")
    options.add_argument("--use-fake-ui-for-media-stream")
    options.add_argument("--use-fake-device-for-media-stream")
    options.add_argument("--auto-select-desktop-capture-source=Entire screen")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-extensions")
    options.binary_location = "/opt/chrome-linux64/chrome"
    chromedriver_path = os.environ.get("CHROMEDRIVER_PATH", "/usr/local/bin/chromedriver")
    return webdriver.Chrome(service=Service(chromedriver_path), options=options)


def get_share_link(driver):
    current = driver.current_url
    if "screensharing.net" in current and current not in (
        "https://screensharing.net", "https://screensharing.net/", "about:blank"
    ) and len(current) > 30:
        return current
    for el in driver.find_elements(By.TAG_NAME, "input"):
        val = el.get_attribute("value") or ""
        if "screensharing.net" in val and len(val) > 25:
            return val.strip()
    for el in driver.find_elements(By.XPATH, "//*[contains(text(),'screensharing.net/')]"):
        txt = el.text.strip()
        if txt.startswith("http") and len(txt) > 25:
            return txt
    for el in driver.find_elements(By.XPATH,
        "//*[@data-clipboard-text or contains(@class,'copy') or contains(@class,'share-link') or contains(@class,'room')]"):
        val = (el.get_attribute("data-clipboard-text") or
               el.get_attribute("href") or
               el.get_attribute("value") or el.text or "")
        if "screensharing.net" in val and len(val) > 25:
            return val.strip()
    return None


def run_screenshare_job(job_id, url):
    log.info(f"[{job_id}] Starting job for: {url}")
    with jobs_lock:
        jobs[job_id]["status"] = "running"

    driver = None
    try:
        driver = make_driver()
        with drivers_lock:
            drivers[job_id] = driver
        log.info(f"[{job_id}] Chrome started")

        driver.get(url)
        time.sleep(2)
        target_tab = driver.current_window_handle
        log.info(f"[{job_id}] Target loaded: '{driver.title}'")

        driver.execute_script("window.open('https://screensharing.net', '_blank');")
        time.sleep(2)
        tabs = driver.window_handles
        screenshare_tab = [t for t in tabs if t != target_tab][0]
        driver.switch_to.window(screenshare_tab)
        time.sleep(5)
        log.info(f"[{job_id}] screensharing.net loaded")

        wait = WebDriverWait(driver, 15)
        try:
            btn = wait.until(EC.element_to_be_clickable(
                (By.XPATH, "//button[contains(text(),'SHARE MY SCREEN') or contains(text(),'Share My Screen')]")
            ))
            log.info(f"[{job_id}] Clicking: '{btn.text}'")
            btn.click()
        except Exception as e:
            log.warning(f"[{job_id}] Button not found, trying fallback: {e}")
            for el in driver.find_elements(By.TAG_NAME, "button"):
                if el.is_displayed() and el.text.strip():
                    log.info(f"[{job_id}] Fallback: '{el.text}'")
                    el.click()
                    break

        time.sleep(5)

        share_link = None
        for attempt in range(60):
            try:
                share_link = get_share_link(driver)
            except Exception:
                pass
            if share_link:
                log.info(f"[{job_id}] Got link: {share_link}")
                break
            log.info(f"[{job_id}] Attempt {attempt+1}/60 — {driver.current_url}")
            time.sleep(1)

        if share_link:
            with jobs_lock:
                jobs[job_id]["status"] = "done"
                jobs[job_id]["share_link"] = share_link
            log.info(f"[{job_id}] ✅ Keeping Chrome open...")

            while True:
                time.sleep(5)
                with jobs_lock:
                    if jobs[job_id].get("stop_requested"):
                        break
                try:
                    _ = driver.current_url
                except Exception:
                    break
        else:
            try:
                log.info(f"[{job_id}] Final URL: {driver.current_url}")
                log.info(f"[{job_id}] Page source snippet: {driver.page_source[:1000]}")
            except Exception:
                pass
            with jobs_lock:
                jobs[job_id]["status"] = "error"
                jobs[job_id]["error"]  = "Could not get share link from screensharing.net"

    except Exception as e:
        log.error(f"[{job_id}] Exception: {e}", exc_info=True)
        with jobs_lock:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"]  = str(e)
    finally:
        with drivers_lock:
            drivers.pop(job_id, None)
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        with jobs_lock:
            if jobs.get(job_id, {}).get("status") == "done":
                jobs[job_id]["status"] = "stopped"


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
        jobs[job_id] = {"status": "queued", "url": url, "share_link": None, "error": None, "stop_requested": False}
    threading.Thread(target=run_screenshare_job, args=(job_id, url), daemon=True).start()
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
    return jsonify({"status": "stopping"})


@app.route("/api/debug", methods=["GET"])
def debug():
    driver = None
    try:
        driver = make_driver()
        driver.get("https://screensharing.net")
        time.sleep(4)
        return jsonify({
            "url": driver.current_url,
            "title": driver.title,
            "buttons": [{"text": el.text.strip(), "visible": el.is_displayed()}
                        for el in driver.find_elements(By.TAG_NAME, "button")],
            "snippet": driver.page_source[:2000],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if driver:
            try: driver.quit()
            except Exception: pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
