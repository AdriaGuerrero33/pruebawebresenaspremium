"""Google Places API fallback when Playwright scraping is blocked."""

import re
import requests


def _place_id_from_url(url: str) -> str | None:
    # Google Maps URLs embed the Place ID in the data parameter: !1sChIJ...
    m = re.search(r'!1s(ChIJ[a-zA-Z0-9_\-]+)', url)
    return m.group(1) if m else None


def _place_name_from_url(url: str) -> str | None:
    m = re.search(r'/maps/place/([^/@?]+)', url)
    if m:
        return m.group(1).replace('+', ' ').replace('%20', ' ')
    return None


def fetch_via_places_api(url: str, api_key: str) -> dict:
    """
    Return {rating, total_reviews, stars} using the Google Places API.
    stars will always be empty — the API does not expose the histogram.
    """
    place_id = _place_id_from_url(url)

    if not place_id:
        name = _place_name_from_url(url)
        if not name:
            return {"rating": None, "total_reviews": None, "stars": {}}
        resp = requests.get(
            "https://maps.googleapis.com/maps/api/place/findplacefromtext/json",
            params={"input": name, "inputtype": "textquery", "fields": "place_id", "key": api_key},
            timeout=10,
        )
        candidates = resp.json().get("candidates", [])
        if not candidates:
            return {"rating": None, "total_reviews": None, "stars": {}}
        place_id = candidates[0]["place_id"]

    resp = requests.get(
        "https://maps.googleapis.com/maps/api/place/details/json",
        params={"place_id": place_id, "fields": "rating,user_ratings_total", "key": api_key},
        timeout=10,
    )
    result = resp.json().get("result", {})
    return {
        "rating": result.get("rating"),
        "total_reviews": result.get("user_ratings_total"),
        "stars": {},
        "_source": "places_api",
    }
