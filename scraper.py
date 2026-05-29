#!/usr/bin/env python3
"""Google Maps review scraper — extracts rating, total reviews, and star breakdown."""

import sys
import os
import re
import json
import glob
import argparse
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout


def _find_chromium_executable() -> str | None:
    """Find an available Chromium executable, searching known Playwright browser paths."""
    candidates = [
        # Standard Playwright managed path (set via env var or default)
        *glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"),
        *glob.glob("/root/.cache/ms-playwright/chromium-*/chrome-linux/chrome"),
        *glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux/chrome")),
    ]
    for path in sorted(candidates, reverse=True):  # prefer highest build number
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


# ── Helpers ─────────────────────────────────────────────────────────────────

def _parse_number(text: str) -> int | None:
    """Convert '1.234' or '1,234' or '1234' to int, return None if not parseable."""
    cleaned = re.sub(r"\D", "", text)
    return int(cleaned) if cleaned else None


def _try_rating(text: str) -> float | None:
    m = re.search(r"\b([1-5][,.][\d])\b", text)
    return float(m.group(1).replace(",", ".")) if m else None


def _dismiss_consent(page):
    """Try every known selector for Google's consent/cookie page."""
    consent_selectors = [
        # Spanish
        'button:has-text("Aceptar todo")',
        'button:has-text("Acepto")',
        'button:has-text("Aceptar")',
        # English
        'button:has-text("Accept all")',
        'button:has-text("I agree")',
        'button:has-text("Accept")',
        # Google consent page stable IDs
        '#L2AGLb',
        'button.tHlp8d',
        '[aria-label="Aceptar todo"]',
        '[aria-label="Accept all"]',
        # Fallback: last button in a form (usually the accept button)
        'form button:last-of-type',
        'form:has(button) button:last-child',
    ]
    for selector in consent_selectors:
        try:
            btn = page.locator(selector).first
            if btn.count() > 0 and btn.is_visible(timeout=1500):
                btn.click()
                page.wait_for_timeout(2000)
                print(f"Consentimiento aceptado con selector: {selector}")
                return True
        except Exception:
            pass
    return False


# ── Core scraper ─────────────────────────────────────────────────────────────

def scrape_google_maps_reviews(url: str, headless: bool = True, debug: bool = False) -> dict:
    with sync_playwright() as p:
        launch_kwargs: dict = {
            "headless": headless,
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--ignore-certificate-errors",
                # Critical in Docker/Railway: /dev/shm is tiny (64MB) and Chromium
                # crashes silently without this, returning empty data.
                "--disable-dev-shm-usage",
                "--disable-gpu",
                # Anti-bot detection flags
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-infobars",
            ],
        }
        # If the default managed browser isn't available, find one on disk
        try:
            browser = p.chromium.launch(**launch_kwargs)
        except Exception:
            exe = _find_chromium_executable()
            if exe:
                print(f"Usando chromium alternativo: {exe}")
                launch_kwargs["executable_path"] = exe
            browser = p.chromium.launch(**launch_kwargs)

        context = browser.new_context(
            locale="es-ES",
            timezone_id="Europe/Madrid",
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            extra_http_headers={
                "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
            },
        )
        # Mask navigator.webdriver to avoid bot detection
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        # Pre-set the SOCS consent cookie so Google skips the cookie wall entirely.
        # This is the most reliable way to avoid the consent page on datacenter IPs,
        # where the clickable consent dialog is often the point of failure.
        _socs = "CAISHAgBEhJnd3NfMjAyMzA4MDktMF9SQzEaAmVzIAEaBgiA_LynBg"
        context.add_cookies([
            {"name": "SOCS", "value": _socs, "domain": ".google.com",
             "path": "/", "secure": True, "sameSite": "None"},
            {"name": "SOCS", "value": _socs, "domain": ".google.es",
             "path": "/", "secure": True, "sameSite": "None"},
            {"name": "CONSENT", "value": "YES+", "domain": ".google.com",
             "path": "/", "secure": True, "sameSite": "None"},
        ])

        page = context.new_page()

        print(f"Abriendo: {url}")
        try:
            page.goto(url, wait_until="networkidle", timeout=45000)
        except PlaywrightTimeout:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except PlaywrightTimeout:
                pass

        print(f"URL tras redirección: {page.url}")

        # Dismiss consent page if shown
        _dismiss_consent(page)

        # If we ended up on a consent/accounts page, navigate back
        if "consent.google" in page.url or "accounts.google" in page.url:
            print("Página de consentimiento detectada, reintentando...")
            try:
                page.goto(url, wait_until="networkidle", timeout=45000)
            except PlaywrightTimeout:
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                except PlaywrightTimeout:
                    pass
            _dismiss_consent(page)

        print(f"URL final: {page.url}")

        # Click reviews tab if visible
        try:
            for selector in [
                'button[aria-label*="Reseñas"]',
                'button[aria-label*="reseñas"]',
                'button[aria-label*="Reviews"]',
                'button[data-tab-index="1"]',
            ]:
                btn = page.locator(selector).first
                if btn.count() > 0 and btn.is_visible(timeout=1000):
                    btn.click()
                    page.wait_for_timeout(2000)
                    break
        except Exception:
            pass

        # Wait for main panel
        try:
            page.wait_for_selector('[role="main"]', timeout=15000)
        except PlaywrightTimeout:
            pass
        page.wait_for_timeout(3000)

        screenshot_b64 = None
        if debug:
            import base64
            screenshot_b64 = base64.b64encode(page.screenshot(full_page=False)).decode()

        result = _extract_data(page)
        print(f"Resultado del scraper: rating={result.get('rating')}, total={result.get('total_reviews')}, stars={result.get('stars')}")

        browser.close()
        if debug:
            result["_debug_url"] = page.url
            result["_debug_screenshot"] = screenshot_b64
        return result


def _extract_data(page) -> dict:
    body_text = ""
    try:
        body_text = page.inner_text('[role="main"]')
    except Exception:
        try:
            body_text = page.inner_text("body")
        except Exception:
            pass

    html_source = ""
    try:
        html_source = page.content()
    except Exception:
        pass

    # ── Rating ───────────────────────────────────────────────────────────────
    rating = None

    # Strategy 1: aria-label on elements that specifically mention stars/estrellas
    try:
        for el in page.locator('[aria-label]').all():
            label = el.get_attribute("aria-label") or ""
            # Must mention stars/estrellas to avoid false positives like "4.3 km"
            if re.search(r"estrella|star", label, re.IGNORECASE):
                r = _try_rating(label)
                if r:
                    rating = r
                    break
    except Exception:
        pass

    # Strategy 2: standalone "4.3" or "4,3" in aria-hidden spans
    if rating is None:
        try:
            for text in page.locator('span[aria-hidden="true"]').all_text_contents():
                r = _try_rating(text.strip())
                if r:
                    rating = r
                    break
        except Exception:
            pass

    # Strategy 3: look for rating in JSON-LD embedded in HTML
    if rating is None:
        try:
            m = re.search(r'"ratingValue"\s*:\s*"?([1-5][.,]\d)"?', html_source)
            if m:
                rating = float(m.group(1).replace(",", "."))
        except Exception:
            pass

    # Strategy 4: scan raw body text — last resort
    if rating is None:
        rating = _try_rating(body_text)

    # ── Total reviews ────────────────────────────────────────────────────────
    total = None

    # Strategy 1: aria-label mentioning reseñas/reviews with a count
    try:
        for el in page.locator('[aria-label]').all():
            label = el.get_attribute("aria-label") or ""
            if re.search(r"rese[ñn]a|review", label, re.IGNORECASE):
                parts = re.split(r"rese[ñn]a|review", label, flags=re.IGNORECASE)
                n = _parse_number(re.sub(r"[^\d]", "", parts[0]))
                if n and n > 0:
                    total = n
                    break
    except Exception:
        pass

    # Strategy 2: JSON-LD reviewCount
    if total is None:
        try:
            m = re.search(r'"reviewCount"\s*:\s*"?(\d+)"?', html_source)
            if m:
                total = int(m.group(1))
        except Exception:
            pass

    # Strategy 3: scan body text for "X reseñas" / "X reviews"
    if total is None:
        for pattern in [
            r"([\d][\d.,\s]*)\s*rese[ñn]as?",
            r"([\d][\d.,\s]*)\s*reviews?",
        ]:
            m = re.search(pattern, body_text, re.IGNORECASE)
            if m:
                total = _parse_number(m.group(1))
                if total:
                    break

    # ── Star breakdown ───────────────────────────────────────────────────────
    stars: dict[int, int | None] = {}

    # Strategy 1: aria-labels like "5 estrellas: 1.234 reseñas"
    try:
        for el in page.locator('[aria-label]').all():
            label = el.get_attribute("aria-label") or ""
            m = re.match(
                r"(\d)\s*(?:estrella[s]?|star[s]?)\s*[:\-,]?\s*([\d.,]+)\s*(?:rese[ñn]a|review)",
                label,
                re.IGNORECASE,
            )
            if m:
                stars[int(m.group(1))] = _parse_number(m.group(2))
    except Exception:
        pass

    # Strategy 2: look in table rows or structured data in aria-labels without "review" keyword
    # Some versions show "5 estrellas, 123" without "reseñas"
    if len(stars) < 3:
        try:
            for el in page.locator('[aria-label]').all():
                label = el.get_attribute("aria-label") or ""
                m = re.match(
                    r"(\d)\s*(?:estrella[s]?|star[s]?)[,:\s]+(\d[\d.,]*)",
                    label,
                    re.IGNORECASE,
                )
                if m:
                    star_n = int(m.group(1))
                    if star_n not in stars:
                        stars[star_n] = _parse_number(m.group(2))
        except Exception:
            pass

    # Strategy 3: parse histogram from body text
    if len(stars) < 3:
        lines = [l.strip() for l in body_text.splitlines() if l.strip()]
        for i, line in enumerate(lines):
            if re.fullmatch(r"[1-5]", line):
                star_n = int(line)
                for j in range(i + 1, min(i + 6, len(lines))):
                    n = _parse_number(lines[j])
                    if n and n > 0 and star_n not in stars:
                        stars[star_n] = n
                        break

    return {
        "rating": rating,
        "total_reviews": total,
        "stars": {n: stars.get(n) for n in (5, 4, 3, 2, 1)},
    }


# ── Output ───────────────────────────────────────────────────────────────────

def print_result(data: dict):
    print("\n" + "=" * 50)
    print("   RESEÑAS GOOGLE MAPS")
    print("=" * 50)
    r = data["rating"]
    t = data["total_reviews"]
    print(f"  Puntuación global:  {r if r is not None else 'No encontrado'}")
    print(f"  Total reseñas:      {t if t is not None else 'No encontrado'}")
    print("-" * 50)
    stars = data.get("stars", {})
    max_val = max((v or 0 for v in stars.values()), default=1) or 1
    for n in (5, 4, 3, 2, 1):
        val = stars.get(n)
        bar_len = int((val or 0) / max_val * 25)
        bar = "█" * bar_len
        count_str = str(val) if val is not None else "N/A"
        print(f"  {n}★  {count_str:>7}  {bar}")
    print("=" * 50 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Extrae reseñas de un negocio en Google Maps"
    )
    parser.add_argument("url", help="Enlace de Google Maps (corto o largo)")
    parser.add_argument("--json", action="store_true", help="Salida en formato JSON")
    parser.add_argument(
        "--visible", action="store_true", help="Abrir navegador visible (debug)"
    )
    args = parser.parse_args()

    data = scrape_google_maps_reviews(args.url, headless=not args.visible)

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print_result(data)


if __name__ == "__main__":
    main()
