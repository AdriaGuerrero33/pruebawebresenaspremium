"""Cálculos exactos del rating y proyecciones para subir de nota en Google Maps.

Google Maps redondea a 1 decimal con redondeo estándar (X.Y5 sube a X.(Y+1)):
  4.30–4.34 → 4.3
  4.35–4.44 → 4.4
  ...
  4.95–5.00 → 5.0
"""

import math


def compute_exact_rating(stars: dict) -> tuple[float | None, int]:
    """Devuelve (rating_exacto, total_reseñas) a partir del histograma."""
    counts = {int(k): (v or 0) for k, v in stars.items()}
    total = sum(counts.values())
    if total == 0:
        return None, 0
    weighted_sum = sum(star * count for star, count in counts.items())
    return weighted_sum / total, total


def reviews_needed_for_target(stars: dict, target_displayed: float) -> int | None:
    """Cuántas reseñas de 5★ se necesitan para que Google muestre `target_displayed`.

    target_displayed: ej. 4.4, 4.5, …, 5.0
    Retorna None si ya se alcanza, o el número entero de reseñas necesarias.
    """
    counts = {int(k): (v or 0) for k, v in stars.items()}
    current_total = sum(counts.values())
    current_sum = sum(s * c for s, c in counts.items())
    if current_total == 0:
        return None

    current_rating = current_sum / current_total
    # Umbral mínimo para que Google muestre target_displayed
    threshold = round(target_displayed - 0.05, 4)

    if current_rating >= threshold:
        return 0

    if target_displayed >= 5.0:
        # Para que muestre 5.0 necesita rating >= 4.95
        threshold = 4.95

    denom = 5.0 - threshold
    if denom <= 0:
        return None

    n = (threshold * current_total - current_sum) / denom
    return max(0, math.ceil(n))


def projection_table(stars: dict) -> list[dict]:
    """Tabla con cuántas reseñas de 5★ faltan para cada hito de 4.4 a 5.0."""
    rating, total = compute_exact_rating(stars)
    if rating is None:
        return []

    rows = []
    # Próximo hito redondeado hacia arriba en pasos de 0.1
    current_displayed = round(rating, 1)
    start = max(4.4, math.floor(current_displayed * 10) / 10 + 0.1)

    target = start
    while target <= 5.0001:
        target = round(target, 2)
        n = reviews_needed_for_target(stars, target)
        if n is not None:
            rows.append(
                {
                    "target": target,
                    "threshold": round(target - 0.05, 3) if target < 5.0 else 4.95,
                    "reviews_needed": n,
                }
            )
        target += 0.1

    return rows


def displayed_rating(exact: float | None) -> float | None:
    """Como lo muestra Google: redondeo a 1 decimal."""
    if exact is None:
        return None
    return round(exact + 1e-9, 1)
