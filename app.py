
import threading
import uuid

from flask import Flask, jsonify, redirect, render_template, request, url_for

from database import Database
from scraper  import Scraper
from search   import search_documents

app = Flask(__name__)

db   = Database("pdf_search.db")
_lock = threading.Lock()
_jobs: dict[str, dict] = {}


# ─── Home ─────────────────────────────────────────────────────────────────────

@app.route("/")
def home():
    stats = db.get_stats()
    return render_template("home.html", stats=stats)


@app.route("/api/stats")
def api_stats():
    """Polled by the home page every few seconds for live updates."""
    return jsonify(db.get_stats())


# ─── Scrapper ─────────────────────────────────────────────────────────────────

@app.route("/scrapper")
def scrapper():
    sources = db.get_sources_with_documents()
    return render_template("scrapper.html", sources=sources)


@app.route("/scrapper/start/<int:source_id>", methods=["POST"])
def start_scrape(source_id: int):
    source = db.get_source(source_id)
    if not source:
        return jsonify({"error": "Source not found"}), 404

    job_id = str(uuid.uuid4())
    with _lock:
        _jobs[job_id] = {
            "status":     "running",
            "docs":       [],
            "source_id":  source_id,
            "last_error": None,
        }

    def run():
        try:
            scraper = Scraper(db)
            scraper.scrape(source["url"], source_id, job_id, _jobs, _lock)
            with _lock:
                _jobs[job_id]["status"] = "done"
        except Exception as exc:
            with _lock:
                _jobs[job_id]["status"]     = "error"
                _jobs[job_id]["last_error"] = str(exc)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return jsonify({"job_id": job_id})


@app.route("/scrapper/job/<job_id>")
def job_status(job_id: str):
    with _lock:
        job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


# ─── Configuracion ────────────────────────────────────────────────────────────

@app.route("/configuration")
def configuration():
    sources = db.get_sources()
    return render_template("configuration.html", sources=sources)


@app.route("/configuration/add", methods=["POST"])
def add_url():
    url = request.form.get("url", "").strip()
    if url:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        db.add_source(url)
    return redirect(url_for("configuration"))


@app.route("/configuration/delete/<int:source_id>", methods=["POST"])
def delete_url(source_id: int):
    db.delete_source(source_id)
    return redirect(url_for("configuration"))


# ─── Search ───────────────────────────────────────────────────────────────────

@app.route("/search")
def search():
    query     = request.args.get("q", "").strip()
    raw_thr   = request.args.get("threshold", "0.50")
    try:
        threshold = max(0.0, min(1.0, float(raw_thr)))
    except ValueError:
        threshold = 0.50

    results = search_documents(db, query, threshold) if query else []
    return render_template(
        "search.html",
        query=query,
        results=results,
        threshold=threshold,
    )


db.add_source(
    "https://fi-ing.unison.mx/acuerdos-de-sesiones-del-h-colegio-de-la-facultad-interdisciplinaria-de-ingenieria-2026/"
)

if __name__ == "__main__":
    app.run(debug=True, use_reloader=False, port=5000)
