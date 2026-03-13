FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    wget curl unzip gnupg xvfb \
    fonts-liberation libappindicator3-1 libasound2 libatk-bridge2.0-0 \
    libatk1.0-0 libcups2 libdbus-1-3 libdrm2 libgbm1 libgtk-3-0 \
    libnspr4 libnss3 libx11-xcb1 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 xdg-utils libxss1 libxtst6 \
    --no-install-recommends && rm -rf /var/lib/apt/lists/*

# Download Chrome AND ChromeDriver as a matched pair
RUN CHROME_VERSION=$(curl -s "https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions.json" \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['channels']['Stable']['version'])") \
    && echo "=== Installing Chrome + ChromeDriver $CHROME_VERSION ===" \
    && wget -q "https://storage.googleapis.com/chrome-for-testing-public/${CHROME_VERSION}/linux64/chrome-linux64.zip" -O /tmp/chrome.zip \
    && unzip -q /tmp/chrome.zip -d /opt/ \
    && rm /tmp/chrome.zip \
    && wget -q "https://storage.googleapis.com/chrome-for-testing-public/${CHROME_VERSION}/linux64/chromedriver-linux64.zip" -O /tmp/chromedriver.zip \
    && unzip -q /tmp/chromedriver.zip -d /tmp/ \
    && mv /tmp/chromedriver-linux64/chromedriver /usr/local/bin/chromedriver \
    && chmod +x /usr/local/bin/chromedriver \
    && rm -rf /tmp/chromedriver.zip /tmp/chromedriver-linux64

# Verify versions match
RUN echo "Chrome: $(/opt/chrome-linux64/chrome --version)" \
    && echo "ChromeDriver: $(chromedriver --version)"

ENV CHROMEDRIVER_PATH=/usr/local/bin/chromedriver
ENV DISPLAY=:99

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8080
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--worker-class", "gthread", "--workers", "1", "--threads", "4", "--timeout", "300", "app:app"]
