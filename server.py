"""
idml2banner — Flask API
Endpoints:
  POST /upload          — sube un .idml, encola el trabajo
  GET  /status/<job_id> — estado del trabajo
  GET  /download/<file> — descarga el zip generado
  GET  /health          — healthcheck
"""

import os
import uuid
from flask import Flask, request, jsonify, send_from_directory, abort
from redis import Redis
from rq import Queue
from rq.job import Job

app = Flask(__name__)

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/app/uploads")
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "/app/output")
REDIS_URL  = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

redis_conn = Redis.from_url(REDIS_URL)
q = Queue(connection=redis_conn)


def process_banner(idml_path, sizes, click_url):
    """Task executed by the worker."""
    from idml2banner import extract, scale_layout, render, IAB_SIZES
    import json

    layout = extract(idml_path)
    targets = []
    for s in sizes.split(","):
        s = s.strip()
        if "x" in s:
            w, h = s.split("x")
            targets.append((int(w), int(h)))

    if not targets:
        src_w = int(layout["canvas"]["width"])
        src_h = int(layout["canvas"]["height"])
        targets = [(src_w, src_h)]

    results = []
    for tw, th in targets:
        src_w = int(layout["canvas"]["width"])
        src_h = int(layout["canvas"]["height"])
        if (tw, th) == (src_w, src_h):
            target_layout = layout
        else:
            target_layout = scale_layout(layout, tw, th)

        scaled_json = os.path.join(OUTPUT_DIR, f"layout_{tw}x{th}_{uuid.uuid4().hex[:6]}.json")
        with open(scaled_json, "w") as f:
            json.dump(target_layout, f)

        zip_path = render(scaled_json, output_dir=OUTPUT_DIR, click_url=click_url)
        results.append(os.path.basename(zip_path))

    return results


@app.route("/")
def index():
    from flask import render_template
    return render_template("index.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    f = request.files["file"]
    if not f.filename.endswith(".idml"):
        return jsonify({"error": "Only .idml files accepted"}), 400

    sizes     = request.form.get("sizes", "300x250")
    click_url = request.form.get("click_url", "%%CLICK_URL_UNESC%%")

    filename  = f"{uuid.uuid4().hex}.idml"
    idml_path = os.path.join(UPLOAD_DIR, filename)
    f.save(idml_path)

    job = q.enqueue(process_banner, idml_path, sizes, click_url, job_timeout=300)

    return jsonify({"job_id": job.id, "status": "queued"}), 202


@app.route("/status/<job_id>")
def status(job_id):
    try:
        job = Job.fetch(job_id, connection=redis_conn)
    except Exception:
        return jsonify({"error": "Job not found"}), 404

    resp = {"job_id": job_id, "status": job.get_status()}
    if job.is_finished:
        resp["files"] = job.result
    if job.is_failed:
        resp["error"] = str(job.exc_info)
    return jsonify(resp)


@app.route("/download/<filename>")
def download(filename):
    if not filename.endswith(".zip"):
        abort(400)
    return send_from_directory(OUTPUT_DIR, filename, as_attachment=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
