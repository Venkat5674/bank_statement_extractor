"""
app.py – Flask backend for Bank Statement Extractor.

Routes
------
GET  /                          Serve single-page frontend
POST /api/upload                Accept PDF/image, return {task_id}
GET  /api/status/<task_id>      Poll processing status + result
GET  /api/download/<id>/<fmt>   Download JSON or CSV export
"""

import csv
import io
import json
import logging
import os
import threading
import uuid
from datetime import datetime

from flask import Flask, jsonify, render_template, request, send_file
from flask_cors import CORS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s – %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# ─── Task store (in-memory) ───────────────────────────────────────────────────
_tasks: dict = {}
_tasks_lock = threading.Lock()

# ─── OCR engine (lazy) ────────────────────────────────────────────────────────
_ocr_engine = None
_ocr_lock = threading.Lock()

ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "tiff", "tif", "bmp", "webp"}
MAX_BYTES = 25 * 1024 * 1024  # 25 MB


def get_engine():
    global _ocr_engine
    if _ocr_engine is None:
        with _ocr_lock:
            if _ocr_engine is None:
                from ocr_engine import OCREngine
                use_gpu = os.environ.get("USE_GPU", "0") == "1"
                logger.info("Creating OCREngine (GPU=%s)", use_gpu)
                _ocr_engine = OCREngine(use_gpu=use_gpu)
    return _ocr_engine


# ─── Background worker ────────────────────────────────────────────────────────

def _process(task_id: str, file_bytes: bytes, content_type: str):
    def update(**kw):
        with _tasks_lock:
            _tasks[task_id].update(kw)

    try:
        update(status="ocr", step="Running OCR engines…", progress=20)
        engine = get_engine()
        words = engine.process_file(file_bytes, content_type)
        logger.info("Task %s – OCR produced %d word tokens", task_id, len(words))

        update(status="parsing", step="Parsing transactions…", progress=70)
        from parser import parse_transactions
        transactions = parse_transactions(words)
        logger.info("Task %s – found %d transactions", task_id, len(transactions))

        update(
            status="done",
            step="Complete",
            progress=100,
            result=transactions,
            word_count=len(words),
            finished_at=datetime.now().isoformat(),
        )

    except Exception as exc:
        logger.exception("Task %s failed", task_id)
        update(status="error", step="Failed", error=str(exc))


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify(error="No file field in request"), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify(error="Empty filename"), 400

    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify(error=f"Unsupported file type '.{ext}'. Upload a PDF or image."), 415

    data = f.read()
    if len(data) > MAX_BYTES:
        return jsonify(error=f"File too large ({len(data)//1024} KB). Max 25 MB."), 413

    task_id = str(uuid.uuid4())
    ct = f.content_type or ("application/pdf" if ext == "pdf" else f"image/{ext}")

    with _tasks_lock:
        _tasks[task_id] = {
            "status": "queued",
            "step": "Queued",
            "progress": 0,
            "result": None,
            "error": None,
            "filename": f.filename,
            "created_at": datetime.now().isoformat(),
            "finished_at": None,
            "word_count": 0,
        }

    t = threading.Thread(target=_process, args=(task_id, data, ct), daemon=True)
    t.start()

    logger.info("Task %s created for file '%s'", task_id, f.filename)
    return jsonify(task_id=task_id, filename=f.filename)


@app.route("/api/status/<task_id>")
def status(task_id: str):
    with _tasks_lock:
        task = _tasks.get(task_id)
    if task is None:
        return jsonify(error="Task not found"), 404

    resp = {
        "status":    task["status"],
        "step":      task["step"],
        "progress":  task["progress"],
        "filename":  task["filename"],
    }
    if task["status"] == "done":
        resp["result"]            = task["result"]
        resp["word_count"]        = task["word_count"]
        resp["transaction_count"] = len(task["result"])
        resp["finished_at"]       = task["finished_at"]
    elif task["status"] == "error":
        resp["error"] = task["error"]

    return jsonify(resp)


@app.route("/api/download/<task_id>/<fmt>")
def download(task_id: str, fmt: str):
    with _tasks_lock:
        task = _tasks.get(task_id)
    if task is None:
        return jsonify(error="Task not found"), 404
    if task["status"] != "done":
        return jsonify(error="Processing not yet complete"), 400

    txns = task["result"]
    stem = task["filename"].rsplit(".", 1)[0]

    if fmt == "json":
        payload = json.dumps(txns, indent=2, ensure_ascii=False).encode("utf-8")
        buf = io.BytesIO(payload)
        buf.seek(0)
        return send_file(
            buf, mimetype="application/json",
            as_attachment=True,
            download_name=f"{stem}_transactions.json",
        )

    if fmt == "csv":
        si = io.StringIO()
        fields = ["date", "description", "debit", "credit", "balance"]
        writer = csv.DictWriter(si, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(txns)
        buf = io.BytesIO(si.getvalue().encode("utf-8"))
        buf.seek(0)
        return send_file(
            buf, mimetype="text/csv",
            as_attachment=True,
            download_name=f"{stem}_transactions.csv",
        )

    return jsonify(error="Use 'json' or 'csv'"), 400


# ─── Dev server ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info("Starting dev server on http://localhost:%d", port)
    app.run(debug=True, port=port, use_reloader=False)
