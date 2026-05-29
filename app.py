"""Servidor web Flask para la herramienta de análisis de reseñas de Google Maps."""

import os
import traceback
from flask import Flask, render_template, request, jsonify

from scraper import scrape_google_maps_reviews
from calculator import (
    compute_exact_rating, projection_table, displayed_rating,
    projection_table_from_rating,
)

app = Flask(__name__)

PLACES_API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "")


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
        scraped = {}

    stars = {int(k): v for k, v in (scraped.get("stars") or {}).items() if v is not None}
    scrape_ok = bool(stars or scraped.get("rating") or scraped.get("total_reviews"))

    # Scraping blocked by Google (datacenter IP) — fall back to Places API if key is set
    if not scrape_ok:
        if not PLACES_API_KEY:
            return jsonify({
                "error": (
                    "Google está bloqueando el acceso desde este servidor. "
                    "Configura la variable GOOGLE_PLACES_API_KEY en Railway para "
                    "obtener los datos directamente de la API oficial de Google."
                )
            }), 502

        try:
            from places_api import fetch_via_places_api
            api_data = fetch_via_places_api(url, PLACES_API_KEY)
        except Exception as e:
            traceback.print_exc()
            return jsonify({"error": f"Error en Places API: {e}"}), 500

        rating = api_data.get("rating")
        total = api_data.get("total_reviews")

        if not rating and not total:
            return jsonify({"error": "No se encontró el negocio en Google Maps."}), 404

        projections = projection_table_from_rating(rating, total) if rating and total else []

        return jsonify({
            "scraped_rating": rating,
            "scraped_total": total,
            "exact_rating": rating,
            "displayed_rating": displayed_rating(rating),
            "total_from_stars": None,
            "stars": {},
            "projections": projections,
            "data_source": "places_api",
        })

    # Scraping succeeded — full data including star histogram
    exact, total = compute_exact_rating(stars)
    projections = projection_table(stars) if stars else []

    return jsonify({
        "scraped_rating": scraped.get("rating"),
        "scraped_total": scraped.get("total_reviews"),
        "exact_rating": round(exact, 4) if exact is not None else None,
        "displayed_rating": displayed_rating(exact),
        "total_from_stars": total if total else None,
        "stars": {n: stars.get(n, 0) for n in (5, 4, 3, 2, 1)},
        "projections": projections,
        "data_source": "scraper",
    })


@app.route("/api/debug", methods=["POST"])
def debug():
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
