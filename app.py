"""
Screenshare Bot - Backend API
screensharing.net uses WebRTC which requires a real display.
We use a virtual display (Xvfb) + Chrome in normal mode to work around this.
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
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

jobs = {}
jobs_lock = threading.Lock()

# Start virtual display once at startup
DISPLAY = ":99"
def start_xvfb():
    try:
        subprocess.Popen(
            ["Xvfb", DISPLAY, "-screen", "0", "1280x900x24", "-ac"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        time.sleep(2)
        log.info("Xvfb virtual display started on " + DISPLAY)
    except Exception as e:
        log.warning(f"Xvfb not available: {e} — falling back to headless")

start_xvfb()


def make_driver():
    options = Options()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-setuid-sandbox")
    options.add_argument("--window-size=1280,900")
    options.add_argument("--use-fake-ui-for-media-stream")
    options.add_argument("--use-fake-device-for-media-stream")
    options.add_argument("--auto-select-desktop-capture-source=Entire screen")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-extensions")
    options.add_argument("--remote-debugging-port=0")
    options.binary_location = "/opt/chrome-linux64/chrome"

    # Use virtual display if available, otherwise headless
    if os.path.exists("/tmp/.X99-lock") or os.environ.get("DISPLAY"):
        os.environ["DISPLAY"] = DISPLAY
        log.info("Using virtual display")
    else:
        options.add_argument("--headless=new")
        log.info("Using headless mode")

    chromedriver_path = os.environ.get("CHROMEDRIVER_PATH", "/usr/local/bin/chromedriver")
    service = Service(chromedriver_path)
    return webdriver.Chrome(service=service, options=options)


def run_screenshare_job(job_id, url):
    log.info(f"[{job_id}] Starting job for: {url}")
    with jobs_lock:
        jobs[job_id]["status"] = "running"

    driver = None
    try:
        driver = make_driver()
        log.info(f"[{job_id}] Chrome started")

        # Step 1: Open target URL in tab 1
        driver.get(url)
        time.sleep(2)
        target_tab = driver.current_window_handle
        log.info(f"[{job_id}] Target URL loaded")

        # Step 2: Open screensharing.net in tab 2
        driver.execute_script("window.open('https://screensharing.net', '_blank');")
        time.sleep(2)
        tabs = driver.window_handles
        screenshare_tab = [t for t in tabs if t != target_tab][0]
        driver.switch_to.window(screenshare_tab)
        time.sleep(4)
        log.info(f"[{job_id}] screensharing.net loaded: {driver.title}")

        # Step 3: Click SHARE MY SCREEN
        wait = WebDriverWait(driver, 15)
        try:
            btn = wait.until(EC.element_to_be_clickable(
                (By.XPATH, "//button[contains(text(),'SHARE MY SCREEN') or contains(text(),'Share My Screen') or contains(text(),'share my screen')]")
            ))
            log.info(f"[{job_id}] Found button: '{btn.text}'")
            btn.click()
            log.info(f"[{job_id}] Clicked share button")
        except Exception as e:
            log.warning(f"[{job_id}] Primary button not found ({e}), trying fallback")
            # Fallback: click first visible button
            for el in driver.find_elements(By.TAG_NAME, "button"):
                if el.is_displayed() and el.text.strip():
                    log.info(f"[{job_id}] Fallback click: '{el.text}'")
                    el.click()
                    break

        time.sleep(5)

        # Step 4: Handle screen picker dialog if it appears
        # screensharing.net redirects to a room URL after sharing starts
        log.info(f"[{job_id}] Waiting for room URL...")
        share_link = None

        for attempt in range(30):
            current = driver.current_url
            log.info(f"[{job_id}] Attempt {attempt+1}: {current}")

            # Room URL looks like screensharing.net/room/xxxx or screensharing.net/s/xxxx
            if "screensharing.net" in current and current not in (
                "https://screensharing.net",
                "https://screensharing.net/",
                "about:blank"
            ) and len(current) > 30:
                share_link = current
                log.info(f"[{job_id}] Got room URL: {share_link}")
                break

            # Check for any input/element showing a shareable link
            try:
                for el in driver.find_elements(By.TAG_NAME, "input"):
                    val = el.get_attribute("value") or ""
                    if "screensharing.net" in val and len(val) > 25:
                        share_link = val.strip()
                        log.info(f"[{job_id}] Got link from input: {share_link}")
                        break
            except Exception:
                pass
            if share_link:
                break

            try:
                for el in driver.find_elements(By.XPATH, "//*[contains(text(),'screensharing.net/')]"):
                    txt = el.text.strip()
                    if txt.startswith("http") and len(txt) > 25:
                        share_link = txt
                        log.info(f"[{job_id}] Got link from text: {share_link}")
                        break
            except Exception:
                pass
            if share_link:
                break

            # Check all clickable elements for copy/link buttons
            try:
                for el in driver.find_elements(By.XPATH, "//*[contains(@class,'copy') or contains(@id,'copy') or contains(@class,'share-link')]"):
                    val = el.get_attribute("data-clipboard-text") or el.get_attribute("value") or el.text or ""
                    if "screensharing.net" in val:
                        share_link = val.strip()
                        log.info(f"[{job_id}] Got link from copy element: {share_link}")
                        break
            except Exception:
                pass
            if share_link:
                break

            time.sleep(1)

        if share_link:
            with jobs_lock:
                jobs[job_id]["status"] = "done"
                jobs[job_id]["share_link"] = share_link
        else:
            # Dump final page state for debugging
            try:
                log.info(f"[{job_id}] Final URL: {driver.current_url}")
                log.info(f"[{job_id}] Final title: {driver.title}")
                log.info(f"[{job_id}] Page source: {driver.page_source[:2000]}")
            except Exception:
                pass
            msg = "Screen shared but could not get link — screensharing.net may require manual tab selection. Try the /api/debug endpoint."
            log.error(f"[{job_id}] {msg}")
            with jobs_lock:
                jobs[job_id]["status"] = "error"
                jobs[job_id]["error"] = msg

    except Exception as e:
        log.error(f"[{job_id}] Exception: {e}", exc_info=True)
        with jobs_lock:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"] = str(e)
    finally:
        if driver:
            try:
                driver.quit()
                log.info(f"[{job_id}] Chrome closed")
            except Exception:
                pass


@app.route("/api/debug", methods=["GET"])
def debug():
    driver = None
    try:
        driver = make_driver()
        driver.get("https://screensharing.net")
        time.sleep(4)
        result = {
            "url": driver.current_url,
            "title": driver.title,
            "buttons": [{"text": el.text.strip(), "id": el.get_attribute("id"), "class": el.get_attribute("class"), "visible": el.is_displayed()} for el in driver.find_elements(By.TAG_NAME, "button")],
            "inputs": [{"type": el.get_attribute("type"), "id": el.get_attribute("id"), "placeholder": el.get_attribute("placeholder"), "value": el.get_attribute("value")} for el in driver.find_elements(By.TAG_NAME, "input")],
            "page_source_snippet": driver.page_source[:5000],
        }
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if driver:
            try: driver.quit()
            except Exception: pass


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/api/screenshare", methods=["POST"])
def start_screenshare():
    data = request.get_json()
    url = (data or {}).get("url", "").strip()
    if not url:
        return jsonify({"error": "Missing URL"}), 400
    if not url.startswith("http"):
        url = "https://" + url
    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {"status": "queued", "url": url, "share_link": None, "error": None}
    threading.Thread(target=run_screenshare_job, args=(job_id, url), daemon=True).start()
    log.info(f"Job {job_id} queued for {url}")
    return jsonify({"job_id": job_id})


@app.route("/api/job/<job_id>", methods=["GET"])
def get_job(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
