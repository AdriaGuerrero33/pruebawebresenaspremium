#!/usr/bin/env bash
# Instala dependencias y el navegador Chromium para Playwright
set -e

echo "Instalando dependencias Python..."
pip install -r requirements.txt

echo "Instalando Chromium para Playwright..."
playwright install chromium

echo ""
echo "✓ Listo. Uso:"
echo "  python scraper.py \"<url_google_maps>\""
echo "  python scraper.py \"<url_google_maps>\" --json"
echo "  python scraper.py \"<url_google_maps>\" --visible   # abre el navegador"
