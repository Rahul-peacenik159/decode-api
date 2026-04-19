"""
Decode Pipeline API Server
Wraps pipeline.py as an async HTTP API for Make.com automation.

Endpoints:
  POST /run        {"url": "...", "slug": "..."}  → {"job_id": "...", "status": "queued"}
  GET  /status/:id                                → {"status": "running|done|failed", ...}
  GET  /download/:id                              → binary .pptx file
  GET  /health                                    → {"ok": true}
"""

import os
import subprocess
import threading
import uuid
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file

app = Flask(__name__)

# In-memory job store (single instance — Railway free tier)
jobs: dict = {}

# Auth: all endpoints require X-Api-Key header
API_SECRET = os.environ.get("API_SECRET", "")
BASE_DIR    = Path(__file__).parent          # /app in Docker
REPO1       = BASE_DIR / "website-decoder"
REPO2       = BASE_DIR / "pptx-builder"
REPO3       = BASE_DIR / "social-decoder"
PIPELINE    = BASE_DIR / "pipeline.py"


def check_auth():
    if API_SECRET and request.headers.get("X-Api-Key") != API_SECRET:
        abort(401, description="Invalid or missing X-Api-Key header")


def _run_pipeline(job_id: str, url: str, slug: str):
    """Background thread — runs pipeline.py and updates jobs dict."""
    output_dir = Path(f"/tmp/jobs/{job_id}")
    output_dir.mkdir(parents=True, exist_ok=True)
    jobs[job_id]["status"] = "running"

    env = {
        **os.environ,
        "ANTHROPIC_API_KEY":   os.environ.get("ANTHROPIC_API_KEY", ""),
        "SCRAPINGDOG_API_KEY": os.environ.get("SCRAPINGDOG_API_KEY", ""),
    }

    try:
        result = subprocess.run(
            [
                "python3", str(PIPELINE),
                "--url",    url,
                "--slug",   slug,
                "--output", str(output_dir),
                "--repo1",  str(REPO1),
                "--repo2",  str(REPO2),
                "--repo3",  str(REPO3),
            ],
            capture_output=True,
            text=True,
            timeout=600,      # 10-minute hard cap
            env=env,
        )

        if result.returncode == 0:
            pptx_files = sorted(output_dir.glob("*.pptx"))
            if pptx_files:
                pptx_path = pptx_files[-1]
                jobs[job_id].update({
                    "status":        "done",
                    "pptx_path":     pptx_path,
                    "pptx_filename": pptx_path.name,
                })
            else:
                jobs[job_id].update({
                    "status": "failed",
                    "error":  "Pipeline succeeded but no .pptx found in output",
                    "stdout": result.stdout[-2000:],
                })
        else:
            jobs[job_id].update({
                "status": "failed",
                "error":  result.stderr[-2000:] or result.stdout[-1000:],
            })

    except subprocess.TimeoutExpired:
        jobs[job_id].update({"status": "failed", "error": "Pipeline timed out (>10 min)"})
    except Exception as exc:
        jobs[job_id].update({"status": "failed", "error": str(exc)})


# ─── Routes ──────────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return jsonify({"ok": True, "jobs": len(jobs)})


@app.route("/run", methods=["POST"])
def start_job():
    check_auth()

    data = request.get_json(silent=True) or {}
    url  = (data.get("url")  or "").strip()
    slug = (data.get("slug") or "").strip()

    if not url:
        return jsonify({"error": "url is required"}), 400
    if not url.startswith("http"):
        return jsonify({"error": "url must start with http:// or https://"}), 400
    if not slug:
        return jsonify({"error": "slug is required"}), 400

    # Reject if another job for the same slug is already running
    for j in jobs.values():
        if j.get("slug") == slug and j.get("status") in ("queued", "running"):
            return jsonify({"error": f"A job for slug '{slug}' is already in progress"}), 409

    job_id = str(uuid.uuid4())
    jobs[job_id] = {"status": "queued", "url": url, "slug": slug}

    t = threading.Thread(target=_run_pipeline, args=(job_id, url, slug), daemon=True)
    t.start()

    return jsonify({"job_id": job_id, "status": "queued"}), 202


@app.route("/status/<job_id>")
def get_status(job_id: str):
    check_auth()

    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404

    resp: dict = {"status": job["status"]}
    if job.get("pptx_filename"):
        resp["pptx_filename"] = job["pptx_filename"]
    if job.get("error"):
        resp["error"] = job["error"]

    return jsonify(resp)


@app.route("/download/<job_id>")
def download(job_id: str):
    check_auth()

    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    if job["status"] != "done":
        return jsonify({"error": f"job status is '{job['status']}', not 'done'"}), 404

    pptx_path: Path = job["pptx_path"]
    if not pptx_path.exists():
        return jsonify({"error": "file no longer exists on server"}), 410

    return send_file(
        pptx_path,
        as_attachment=True,
        download_name=job["pptx_filename"],
        mimetype="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
