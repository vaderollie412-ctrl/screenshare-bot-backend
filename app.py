"""
Screenshare Bot - Backend API (with debug endpoint)
"""
import os
import time
import uuid
import logging
import threading
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


def get_chrome_options():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-setuid-sandbox")
    options.add_argument("--window-size=1280,900")
    options.add_argument("--use-fake-ui-for-media-stream")
    options.add_argument("--use-fake-device-for-media-stream")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-extensions")
    options.add_argument("--remote-debugging-port=0")
    options.binary_location = "/opt/chrome-linux64/chrome"
    return options


def make_driver():
    options = get_chrome_options()
    chromedriver_path = os.environ.get("CHROMEDRIVER_PATH", "/usr/local/bin/chromedriver")
    service = Service(chromedriver_path)
    return webdriver.Chrome(service=service, options=options)


# ── DEBUG ENDPOINT ────────────────────────────────────────────────────────────
@app.route("/api/debug", methods=["GET"])
def debug():
    """Visit screensharing.net and dump all buttons, inputs, links so we can fix selectors."""
    driver = None
    try:
        driver = make_driver()
        driver.get("https://screensharing.net")
        time.sleep(4)

        result = {
            "url": driver.current_url,
            "title": driver.title,
            "buttons": [],
            "inputs": [],
            "links": [],
            "page_source_snippet": driver.page_source[:5000],
        }

        for el in driver.find_elements(By.TAG_NAME, "button"):
            result["buttons"].append({
                "text": el.text.strip(),
                "id": el.get_attribute("id"),
                "class": el.get_attribute("class"),
                "visible": el.is_displayed(),
            })

        for el in driver.find_elements(By.TAG_NAME, "input"):
            result["inputs"].append({
                "type": el.get_attribute("type"),
                "id": el.get_attribute("id"),
                "name": el.get_attribute("name"),
                "placeholder": el.get_attribute("placeholder"),
                "value": el.get_attribute("value"),
                "class": el.get_attribute("class"),
            })

        for el in driver.find_elements(By.TAG_NAME, "a"):
            href = el.get_attribute("href") or ""
            if href:
                result["links"].append({"text": el.text.strip(), "href": href})

        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


# ── SCREENSHARE JOB ───────────────────────────────────────────────────────────
def run_screenshare_job(job_id, url):
    log.info(f"[{job_id}] Starting job for URL: {url}")
    with jobs_lock:
        jobs[job_id]["status"] = "running"

    driver = None
    try:
        driver = make_driver()
        log.info(f"[{job_id}] Chrome started")
        wait = WebDriverWait(driver, 20)

        log.info(f"[{job_id}] Opening target URL: {url}")
        driver.get(url)
        time.sleep(2)
        target_tab = driver.current_window_handle

        log.info(f"[{job_id}] Opening screensharing.net")
        driver.execute_script("window.open('https://screensharing.net', '_blank');")
        time.sleep(2)
        tabs = driver.window_handles
        screenshare_tab = [t for t in tabs if t != target_tab][0]
        driver.switch_to.window(screenshare_tab)
        time.sleep(4)
        log.info(f"[{job_id}] Page title: {driver.title}, URL: {driver.current_url}")

        log.info(f"[{job_id}] Looking for Share Screen button")
        clicked = False
        selectors = [
            (By.XPATH, "//button[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'share screen')]"),
            (By.XPATH, "//button[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'share')]"),
            (By.XPATH, "//button[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'start')]"),
            (By.XPATH, "//button[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'screen')]"),
            (By.XPATH, "//*[@id='share' or @id='start' or @id='shareBtn' or @id='startBtn']"),
            (By.XPATH, "//button"),
        ]
        for by, selector in selectors:
            try:
                els = driver.find_elements(by, selector)
                for el in els:
                    if el.is_displayed():
                        log.info(f"[{job_id}] Clicking: '{el.text}' id={el.get_attribute('id')}")
                        el.click()
                        clicked = True
                        break
            except Exception:
                pass
            if clicked:
                break

        if not clicked:
            log.warning(f"[{job_id}] No button found - dumping all buttons:")
            for el in driver.find_elements(By.TAG_NAME, "button"):
                log.info(f"  button text='{el.text}' id={el.get_attribute('id')} class={el.get_attribute('class')}")

        time.sleep(4)

        log.info(f"[{job_id}] Waiting for share link...")
        share_link = None
        for attempt in range(25):
            current = driver.current_url
            log.info(f"[{job_id}] Attempt {attempt+1} URL: {current}")

            if "screensharing.net" in current and current not in (
                "https://screensharing.net", "https://screensharing.net/", "about:blank"
            ):
                share_link = current
                break

            try:
                for el in driver.find_elements(By.TAG_NAME, "input"):
                    val = el.get_attribute("value") or ""
                    if val.startswith("http"):
                        share_link = val.strip()
                        break
            except Exception:
                pass
            if share_link:
                break

            try:
                for el in driver.find_elements(By.XPATH, "//*[contains(text(),'screensharing.net/')]"):
                    txt = el.text.strip()
                    if txt.startswith("http"):
                        share_link = txt
                        break
            except Exception:
                pass
            if share_link:
                break

            time.sleep(1)

        if share_link:
            log.info(f"[{job_id}] Done! Link: {share_link}")
            with jobs_lock:
                jobs[job_id]["status"] = "done"
                jobs[job_id]["share_link"] = share_link
        else:
            try:
                log.info(f"[{job_id}] PAGE SOURCE: {driver.page_source[:3000]}")
            except Exception:
                pass
            msg = "Could not detect share link. Open /api/debug in browser for page structure."
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
            except Exception:
                pass


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

    thread = threading.Thread(target=run_screenshare_job, args=(job_id, url), daemon=True)
    thread.start()
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
