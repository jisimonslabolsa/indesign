"""
idml2banner — Flask API
"""
import os, uuid, json, sys
from flask import Flask, request, jsonify, send_from_directory, render_template
from redis import Redis
from rq import Queue
from rq.job import Job

app = Flask(__name__, static_folder='static', static_url_path='/static')

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/app/uploads")
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "/app/output")
REDIS_URL  = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

FONTS_DIR  = os.environ.get("FONTS_DIR",  "/app/fonts")
IMAGES_DIR = os.environ.get("IMAGES_DIR", "/app/images")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(FONTS_DIR, exist_ok=True)
os.makedirs(IMAGES_DIR, exist_ok=True)

redis_conn = Redis.from_url(REDIS_URL)
q = Queue(connection=redis_conn)


def apply_anchor_rules(layout, target_w, target_h):
    """
    Scale layout to target size applying per-element anchor + scaleMode rules.
    anchor values: stretch, top-left, top-right, top-center,
                   bottom-left, bottom-right, bottom-center, center
    scaleMode values: proportional, fixed, hidden
    """
    import copy
    src_w = layout["canvas"]["width"]
    src_h = layout["canvas"]["height"]
    if src_w == 0 or src_h == 0:
        return layout

    scale_x = target_w / src_w
    scale_y = target_h / src_h
    uniform  = min(scale_x, scale_y)

    scaled = copy.deepcopy(layout)
    scaled["canvas"] = {"width": float(target_w), "height": float(target_h)}

    for el in scaled["elements"]:
        anchor    = el.get("anchor", "top-left")
        scale_mode = el.get("scaleMode", "proportional")

        # Hidden elements
        if scale_mode == "hidden":
            el["_hidden"] = True
            continue

        if scale_mode == "fixed":
            # Keep original size, reposition by anchor
            ew, eh = el["width"], el["height"]
        else:
            # proportional
            if anchor == "stretch":
                ew = round(el["width"]  * scale_x, 2)
                eh = round(el["height"] * scale_y, 2)
            else:
                ew = round(el["width"]  * uniform, 2)
                eh = round(el["height"] * uniform, 2)

        # Scale font sizes for proportional text
        if scale_mode == "proportional":
            for para in el.get("paragraphs", []):
                for run in para.get("runs", []):
                    run["size"] = round(run["size"] * uniform, 1)

        # Compute position by anchor
        if anchor == "stretch":
            ex = round(el["x"] * scale_x, 2)
            ey = round(el["y"] * scale_y, 2)
        elif anchor == "top-left":
            ex = round(el["x"] * scale_x, 2)
            ey = round(el["y"] * scale_y, 2)
        elif anchor == "top-right":
            orig_right = el["x"] + el["width"]
            ex = round(target_w - (src_w - orig_right) * scale_x - ew, 2)
            ey = round(el["y"] * scale_y, 2)
        elif anchor == "top-center":
            orig_cx = el["x"] + el["width"] / 2
            ex = round(orig_cx * scale_x - ew / 2, 2)
            ey = round(el["y"] * scale_y, 2)
        elif anchor == "bottom-left":
            orig_bottom = el["y"] + el["height"]
            ex = round(el["x"] * scale_x, 2)
            ey = round(target_h - (src_h - orig_bottom) * scale_y - eh, 2)
        elif anchor == "bottom-right":
            orig_right  = el["x"] + el["width"]
            orig_bottom = el["y"] + el["height"]
            ex = round(target_w - (src_w - orig_right)  * scale_x - ew, 2)
            ey = round(target_h - (src_h - orig_bottom) * scale_y - eh, 2)
        elif anchor == "bottom-center":
            orig_cx     = el["x"] + el["width"] / 2
            orig_bottom = el["y"] + el["height"]
            ex = round(orig_cx * scale_x - ew / 2, 2)
            ey = round(target_h - (src_h - orig_bottom) * scale_y - eh, 2)
        elif anchor == "center":
            orig_cx = el["x"] + el["width"]  / 2
            orig_cy = el["y"] + el["height"] / 2
            ex = round(orig_cx * scale_x - ew / 2, 2)
            ey = round(orig_cy * scale_y - eh / 2, 2)
        else:
            ex = round(el["x"] * scale_x, 2)
            ey = round(el["y"] * scale_y, 2)

        el["x"], el["y"], el["width"], el["height"] = ex, ey, ew, eh

    # Remove hidden elements
    scaled["elements"] = [e for e in scaled["elements"] if not e.get("_hidden")]
    return scaled


def process_banner_from_layout(layout, sizes_str, click_url, output_dir, fonts=None, image_assignments=None):
    """Worker task: render banners from enriched layout JSON."""
    sys.path.insert(0, "/app")
    from renderer.html5_renderer import render as html_render, IAB_SIZES

    results = []
    targets = []
    for s in sizes_str.split(","):
        s = s.strip()
        if "x" in s:
            w, h = s.split("x")
            targets.append((int(w), int(h)))

    if not targets:
        src_w = int(layout["canvas"]["width"])
        src_h = int(layout["canvas"]["height"])
        targets = [(src_w, src_h)]

    for tw, th in targets:
        src_w = int(layout["canvas"]["width"])
        src_h = int(layout["canvas"]["height"])
        if (tw, th) == (src_w, src_h):
            target_layout = layout
        else:
            target_layout = apply_anchor_rules(layout, tw, th)

        scaled_json = os.path.join(output_dir, f"layout_{tw}x{th}_{uuid.uuid4().hex[:6]}.json")
        with open(scaled_json, "w") as f:
            json.dump(target_layout, f)

        zip_path = html_render(scaled_json, output_dir=output_dir, click_url=click_url, fonts=fonts or [], image_assignments=image_assignments or {})
        results.append(os.path.basename(zip_path))

    return results


def process_banner_from_idml(idml_path, sizes_str, click_url, output_dir):
    """Worker task: parse IDML then render (legacy simple flow)."""
    sys.path.insert(0, "/app")
    from extractor.idml_parser import extract
    layout = extract(idml_path)
    return process_banner_from_layout(layout, sizes_str, click_url, output_dir)


# ── Routes ────────────────────────────────────────────────

@app.route("/")
def index():
    from flask import send_from_directory as sfd; return sfd(os.path.join(app.root_path, "static"), "index.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/analyze", methods=["POST"])
def analyze():
    """Upload IDML, parse it, return layout JSON for the UI editor."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    f = request.files["file"]
    if not f.filename.endswith(".idml"):
        return jsonify({"error": "Only .idml files accepted"}), 400

    filename  = f"{uuid.uuid4().hex}.idml"
    idml_path = os.path.join(UPLOAD_DIR, filename)
    f.save(idml_path)

    try:
        sys.path.insert(0, "/app")
        from extractor.idml_parser import extract
        layout = extract(idml_path)

        if not layout["elements"]:
            return jsonify({"error": "No se encontraron elementos en el IDML"}), 422

        # Auto-name elements
        W = layout["canvas"]["width"]
        H = layout["canvas"]["height"]
        for i, el in enumerate(layout["elements"]):
            if not el.get("name"):
                if el["type"] == "text" and el.get("paragraphs"):
                    t = el["paragraphs"][0]["runs"][0]["text"] if el["paragraphs"][0]["runs"] else ""
                    el["name"] = "Texto: " + t[:22]
                elif el["type"] == "image":
                    el["name"] = "Imagen"
                elif el["type"] == "rectangle" and el["width"] >= W * 0.85 and el["height"] >= H * 0.85:
                    el["name"] = "Fondo"
                elif el["type"] == "rectangle" and el["width"] > 60:
                    el["name"] = f"Rect {el['width']:.0f}×{el['height']:.0f}"
                else:
                    el["name"] = f"{el['type'].capitalize()} #{i}"
            # Default rules
            el.setdefault("anchor", "stretch" if (el["type"] == "rectangle" and el["width"] >= W * 0.85) else "top-left")
            el.setdefault("scaleMode", "proportional")

        return jsonify({"layout": layout, "idml_path": idml_path})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/upload-font", methods=["POST"])
def upload_font():
    """Receive a font file, save it, return family name."""
    if "font" not in request.files:
        return jsonify({"error": "No font file"}), 400
    f = request.files["font"]
    name = f.filename
    ext = os.path.splitext(name)[1].lower()
    if ext not in (".ttf", ".otf", ".woff", ".woff2"):
        return jsonify({"error": "Unsupported font format"}), 400

    # Derive family name from filename (strip extension and style suffixes)
    import re
    family = os.path.splitext(name)[0]
    family = re.sub(r'[-_](bold|italic|regular|light|medium|black|thin|semibold|extrabold|condensed|narrow).*$', '', family, flags=re.IGNORECASE)
    family = family.replace('-', ' ').replace('_', ' ').strip()

    filename = f"{uuid.uuid4().hex[:8]}_{name}"
    font_path = os.path.join(FONTS_DIR, filename)
    f.save(font_path)

    return jsonify({"filename": filename, "family": family, "path": font_path})



@app.route("/upload-image", methods=["POST"])
def upload_image():
    """Receive an image file, save it, return filename and preview URL."""
    if "image" not in request.files:
        return jsonify({"error": "No image file"}), 400
    f = request.files["image"]
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        return jsonify({"error": "Unsupported image format"}), 400
    filename = f"{uuid.uuid4().hex[:8]}_{f.filename}"
    img_path = os.path.join(IMAGES_DIR, filename)
    f.save(img_path)
    return jsonify({"filename": filename, "url": f"/preview-image/{filename}"})


@app.route("/preview-image/<filename>")
def preview_image(filename):
    """Serve uploaded image for preview in UI."""
    return send_from_directory(IMAGES_DIR, filename)



@app.route("/upload", methods=["POST"])
def upload():
    """Receive enriched layout JSON + sizes, enqueue render job."""
    data = request.get_json(silent=True)
    if not data or "layout" not in data:
        return jsonify({"error": "Expected JSON with layout"}), 400

    layout    = data["layout"]
    sizes     = data.get("sizes", "300x250")
    click_url = data.get("click_url", "%%CLICK_URL_UNESC%%")
    fonts            = data.get("fonts", [])
    image_assignments = data.get("image_assignments", {})

    job = q.enqueue(
        process_banner_from_layout,
        layout, sizes, click_url, OUTPUT_DIR, fonts, image_assignments,
        job_timeout=300,
    )
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
        return jsonify({"error": "Invalid file"}), 400
    return send_from_directory(OUTPUT_DIR, filename, as_attachment=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
