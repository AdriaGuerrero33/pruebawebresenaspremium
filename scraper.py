#!/usr/bin/env python3
"""Google Maps review scraper — extracts rating, total reviews, and star breakdown."""

import sys
import re
import json
import argparse
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout


# ── Helpers ─────────────────────────────────────────────────────────────────

def _parse_number(text: str) -> int | None:
    """Convert '1.234' or '1,234' or '1234' to int, return None if not parseable."""
    cleaned = re.sub(r"\D", "", text)
    return int(cleaned) if cleaned else None


def _try_rating(text: str) -> float | None:
    m = re.search(r"\b([1-5][,.][\d])\b", text)
    return float(m.group(1).replace(",", ".")) if m else None


# ── Core scraper ─────────────────────────────────────────────────────────────

def scrape_google_maps_reviews(url: str, headless: bool = True) -> dict:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--ignore-certificate-errors"],
        )
        context = browser.new_context(
            locale="es-ES",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        print(f"Abriendo: {url}")
        try:
            page.goto(url, wait_until="networkidle", timeout=30000)
        except PlaywrightTimeout:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)

        # Dismiss cookies dialog
        for selector in [
            'button:has-text("Aceptar todo")',
            'button:has-text("Accept all")',
            'button:has-text("Acepto")',
            'form:has(button) button:last-child',
        ]:
            try:
                btn = page.locator(selector).first
                if btn.is_visible(timeout=2000):
                    btn.click()
                    page.wait_for_timeout(1000)
                    break
            except Exception:
                pass

        # Wait for main panel to load
        try:
            page.wait_for_selector('[role="main"]', timeout=15000)
        except PlaywrightTimeout:
            pass
        page.wait_for_timeout(2000)

        result = _extract_data(page)
        browser.close()
        return result


def _extract_data(page) -> dict:
    # ── Scroll to load rating histogram ─────────────────────────────────────
    try:
        # Click reviews tab if it exists
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

    body_text = ""
    try:
        body_text = page.inner_text('[role="main"]')
    except Exception:
        body_text = page.inner_text("body")

    # ── Rating ───────────────────────────────────────────────────────────────
    rating = None

    # Strategy 1: element with aria-label containing star/estrella + decimal
    try:
        for el in page.locator('[aria-label]').all():
            label = el.get_attribute("aria-label") or ""
            r = _try_rating(label)
            if r:
                rating = r
                break
    except Exception:
        pass

    # Strategy 2: standalone "4.3" or "4,3" text node in aria-hidden spans
    if rating is None:
        try:
            for text in page.locator('span[aria-hidden="true"]').all_text_contents():
                r = _try_rating(text.strip())
                if r:
                    rating = r
                    break
        except Exception:
            pass

    # Strategy 3: scan raw body text
    if rating is None:
        rating = _try_rating(body_text)

    # ── Total reviews ────────────────────────────────────────────────────────
    total = None

    # Strategy 1: element whose aria-label says "X reseñas / reviews"
    try:
        for el in page.locator('[aria-label]').all():
            label = el.get_attribute("aria-label") or ""
            if re.search(r"rese[ñn]a|review", label, re.IGNORECASE):
                n = _parse_number(re.sub(r"[^\d]", "", re.split(r"rese[ñn]a|review", label, flags=re.IGNORECASE)[0]))
                if n and n > 0:
                    total = n
                    break
    except Exception:
        pass

    # Strategy 2: scan body text for "X reseñas" / "X reviews"
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

    # Strategy 2: parse histogram from body text
    # Google Maps usually shows: "5\n[bar]\n1.234\n4\n[bar]\n987\n..."
    if len(stars) < 3:
        lines = [l.strip() for l in body_text.splitlines() if l.strip()]
        for i, line in enumerate(lines):
            if re.fullmatch(r"[1-5]", line):
                star_n = int(line)
                # The count is usually a few lines ahead
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
