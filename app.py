"""Servidor web Flask para la herramienta de análisis de reseñas de Google Maps."""

import os
import traceback
from flask import Flask, render_template, request, jsonify

from scraper import scrape_google_maps_reviews
from calculator import compute_exact_rating, projection_table, displayed_rating

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/debug")
def debug_page():
    return render_template("debug.html")


@app.route("/api/analyze", methods=["POST"])
def analyze():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()

    if not url:
        return jsonify({"error": "Falta el enlace de Google Maps"}), 400

    if "google" not in url and "maps.app.goo.gl" not in url:
        return jsonify({"error": "El enlace no parece de Google Maps"}), 400

    try:
        scraped = scrape_google_maps_reviews(url, headless=True)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Error al analizar: {e}"}), 500

    stars = {int(k): v for k, v in (scraped.get("stars") or {}).items() if v is not None}

    # If nothing useful was extracted, the scrape failed (cookie wall, bot block,
    # or Google serving an empty page to the datacenter IP). Don't pretend success.
    if not stars and not scraped.get("rating") and not scraped.get("total_reviews"):
        return jsonify({
            "error": (
                "No se pudieron extraer datos de esta ficha. Google puede estar "
                "bloqueando el servidor o el enlace no apunta a un negocio con "
                "reseñas. Prueba con la URL larga de Google Maps (no la acortada) "
                "o usa /debug para ver qué está pasando."
            )
        }), 502

    exact, total = compute_exact_rating(stars)
    projections = projection_table(stars) if stars else []

    return jsonify(
        {
            "scraped_rating": scraped.get("rating"),
            "scraped_total": scraped.get("total_reviews"),
            "exact_rating": round(exact, 4) if exact is not None else None,
            "displayed_rating": displayed_rating(exact),
            "total_from_stars": total if total else None,
            "stars": {n: stars.get(n, 0) for n in (5, 4, 3, 2, 1)},
            "projections": projections,
        }
    )


@app.route("/api/debug", methods=["POST"])
def debug():
    """Igual que /api/analyze pero devuelve screenshot y URL final para diagnosticar el scraper."""
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "Falta el enlace"}), 400
    try:
        scraped = scrape_google_maps_reviews(url, headless=True, debug=True)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    return jsonify({
        "debug_url": scraped.get("_debug_url"),
        "rating": scraped.get("rating"),
        "total_reviews": scraped.get("total_reviews"),
        "stars": scraped.get("stars"),
        "screenshot_b64": scraped.get("_debug_screenshot"),
    })


@app.route("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
