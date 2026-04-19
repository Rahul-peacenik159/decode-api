# Decode Pipeline API — Railway deployment
# Clones all 3 repos from GitHub + installs dependencies + Playwright

FROM python:3.11-slim

# ── System dependencies (Playwright / Chromium needs these) ──────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    git wget curl gcc g++ \
    # Chromium / Playwright runtime libs
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libxkbcommon0 libxdamage1 libgbm1 libxrandr2 \
    libpango-1.0-0 libcairo2 libasound2 libxshmfence1 \
    libxcomposite1 libxfixes3 libxext6 libx11-6 libxcb1 \
    fonts-liberation libfontconfig1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Clone the three repos ─────────────────────────────────────────────────────
# Replace with your actual GitHub usernames/repo names
ARG GITHUB_USER=Rahul-peacenik159
RUN git clone --depth 1 https://github.com/${GITHUB_USER}/website-decoder.git  website-decoder && \
    git clone --depth 1 https://github.com/${GITHUB_USER}/pptx-builder.git     pptx-builder    && \
    git clone --depth 1 https://github.com/${GITHUB_USER}/social-decoder.git   social-decoder

# ── Install Python dependencies ───────────────────────────────────────────────
RUN pip install --no-cache-dir \
    -r website-decoder/requirements.txt \
    -r pptx-builder/requirements.txt \
    -r social-decoder/requirements.txt \
    flask>=3.0 gunicorn>=21.0

# ── Install Playwright + Chromium browser binary ──────────────────────────────
RUN playwright install chromium && \
    playwright install-deps chromium

# ── Copy API server + pipeline orchestrator ───────────────────────────────────
COPY api_server.py pipeline.py ./

# ── Runtime ───────────────────────────────────────────────────────────────────
ENV PYTHONUNBUFFERED=1

# Railway injects $PORT automatically
CMD gunicorn --bind "0.0.0.0:${PORT:-8080}" --timeout 30 --workers 1 api_server:app
