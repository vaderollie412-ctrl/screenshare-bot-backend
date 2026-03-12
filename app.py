"""
Screenshare Bot - Backend API
Handles automation of screensharing.net
"""
import os
import time
import uuid
import threading
from flask import Flask, request, jsonify
from flask_cors import CORS
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

app = Flask(__name__)
CORS(app)

# In-memory job store  { job_id: { status, url, share_link, error } }
jobs = {}
jobs_lock = threading.Lock()


def get_chrome_options():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,900")
    options.add_argument("--use-fake-ui-for-media-stream")
    options.add_argument("--use-fake-device-for-media-stream")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-extensions")
    options.add_experimental_option("prefs", {
        "profile.default_content_setting_values.media_stream_screen": 1,
        "profile.default_content_setting_values.media_stream_mic": 1,
        "profile.default_content_setting_values.media_stream_camera": 1,
    })
    return options


def run_screenshare_job(job_id, url):
    """Background thread: open URL, screenshare it, return the share link."""
    with jobs_lock:
        jobs[job_id]["status"] = "running"

    driver = None
    try:
        options = get_chrome_options()

        # Use chromedriver from environment or let webdriver-manager handle it
        chromedriver_path = os.environ.get("CHROMEDRIVER_PATH")
        if chromedriver_path:
            service = Service(chromedriver_path)
        else:
            from webdriver_manager.chrome import ChromeDriverManager
            service = Service(ChromeDriverManager().install())

        driver = webdriver.Chrome(service=service, options=options)
        wait = WebDriverWait(driver, 20)

        # Step 1: Open the target URL
        driver.get(url)
        time.sleep(2)
        target_tab = driver.current_window_handle

        # Step 2: Open screensharing.net
        driver.execute_script("window.open('https://screensharing.net', '_blank');")
        time.sleep(2)
        tabs = driver.window_handles
        screenshare_tab = [t for t in tabs if t != target_tab][0]
        driver.switch_to.window(screenshare_tab)
        time.sleep(3)

        # Step 3: Click Share Screen
        try:
            share_btn = wait.until(EC.element_to_be_clickable((By.XPATH,
                "//*[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'share screen') or "
                "contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'start sharing') or "
                "contains(@class,'share') or contains(@id,'share')]"
            )))
            share_btn.click()
            time.sleep(3)
        except Exception:
            pass

        # Step 4: Wait for share link to appear
        share_link = None
        for _ in range(20):
            # Check for input with a share URL
            try:
                els = driver.find_elements(By.XPATH, "//input[@type='text' or @type='url' or not(@type)]")
                for el in els:
                    val = el.get_attribute("value") or ""
                    if "http" in val and ("screensharing" in val or "room" in val or "share" in val):
                        share_link = val.strip()
                        break
            except Exception:
                pass

            if not share_link:
                try:
                    els = driver.find_elements(By.XPATH, "//*[contains(@class,'link') or contains(@class,'room') or contains(@class,'share')]")
                    for el in els:
                        txt = el.text or el.get_attribute("value") or ""
                        if "http" in txt:
                            share_link = txt.strip()
                            break
                except Exception:
                    pass

            if share_link:
                break

            # Check if URL changed to a room URL
            current = driver.current_url
            if "screensharing.net" in current and len(current) > 30 and current != "https://screensharing.net":
                share_link = current
                break

            time.sleep(1)

        if share_link:
            with jobs_lock:
                jobs[job_id]["status"] = "done"
                jobs[job_id]["share_link"] = share_link
        else:
            # Fallback: return current URL if it looks like a room
            current = driver.current_url
            if "screensharing.net" in current and current != "https://screensharing.net/":
                with jobs_lock:
                    jobs[job_id]["status"] = "done"
                    jobs[job_id]["share_link"] = current
            else:
                with jobs_lock:
                    jobs[job_id]["status"] = "error"
                    jobs[job_id]["error"] = "Could not detect screenshare link. screensharing.net may have updated its layout."

    except Exception as e:
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

    return jsonify({"job_id": job_id})


@app.route("/api/job/<job_id>", methods=["GET"])
def get_job(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


@app.route("/api/jobs", methods=["GET"])
def list_jobs():
    with jobs_lock:
        return jsonify(list(jobs.values()))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
