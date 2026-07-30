"""
URL List Builder — Desafio Quant AI 2026 (Fase 1: ingesta)

Construye las listas de URLs (data/raw/articles/_url_lists/{TICKER}.csv) que
consume bloomberg_scraper.py, SIN dirigir ningun navegador contra bloomberg.com.

FUENTE: Wayback Machine CDX API (web.archive.org), el indice historico de URLs
capturadas de bloomberg.com. Es un archivo publico de terceros: consultarlo no
toca Bloomberg ni su anti-bot y no requiere sesion. Solo se usa para DESCUBRIR
URLs; el contenido se scrapea despues con bloomberg_scraper.py usando la sesion
de suscriptor.

Fuentes evaluadas y descartadas (2026-07-30):
  - Common Crawl: cero capturas de bloomberg.com/news/articles/* en indices de
    2022, 2023, 2024 y 2025 (Bloomberg bloquea CCBot). Verificado empiricamente.
  - GDELT DOC API: indexa URLs+titulares de Bloomberg y seria complementaria,
    pero su rate-limit publico es agresivo; queda como opcion a explorar.

ESTRATEGIA DE QUERY: una query CDX sobre TODO bloomberg.com/news/* excede el
timeout del servidor. Como las URLs modernas llevan la fecha de publicacion en
el path (/news/articles/YYYY-MM-DD/slug), se consulta por PREFIJOS MENSUALES
(p.ej. url=bloomberg.com/news/articles/2024-03*), que son escaneos chicos
(~5-20 s cada uno). Un mes que falle se reporta y NO aborta el resto; re-correr
el comando es idempotente (merge con dedup por URL).

FILTRADO por ticker: los slugs contienen el nombre de la empresa. Se filtra
server-side por keyword derivada de config/universe.csv (columna `nombre`,
slugificada), extensible con --keywords. Es una heuristica de RECALL
imperfecto: articulos que no mencionan la empresa en el slug no apareceran;
complementar a mano si hace falta.

El headline se deriva del slug como stand-in: el scraper siempre prefiere el
headline real de la pagina y solo usa el del CSV como fallback.

Salida: CSV con las 5 columnas que exige el scraper
    ticker,headline,url,published_date_display,excluded_reason
No-articulos (p.ej. formato viejo /news/YYYY-MM-DD/slug.html) se incluyen CON
excluded_reason (el scraper los ignora, pero quedan auditables). Si el CSV ya
existe, se fusiona (dedup por URL, conservando las filas existentes — pueden
estar curadas a mano).

Uso:
    python src/ingestion/url_list_builder.py --tickers PBR \
        --start-date 2023-07-01 --end-date 2026-07-15
    python src/ingestion/url_list_builder.py --tickers PBR,VALE \
        --start-date 2023-07-01 --end-date 2026-07-15
    python src/ingestion/url_list_builder.py --tickers PBR \
        --start-date 2023-07-01 --end-date 2026-07-15 --keywords petrobras,petroleo
"""

import argparse
import csv
import logging
import random
import re
import sys
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Iterable, Optional
from urllib.parse import urlsplit

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
UNIVERSE_CSV = PROJECT_ROOT / "config" / "universe.csv"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "raw" / "articles" / "_url_lists"

WAYBACK_CDX = "https://web.archive.org/cdx/search/cdx"

URL_LIST_COLUMNS = ["ticker", "headline", "url", "published_date_display", "excluded_reason"]

# Secciones con fecha de publicacion en el path, consultables por prefijo mensual.
URL_SECTIONS = ("articles", "features")

# Cortesia con el archivo publico: delay entre requests + reintentos suaves.
REQUEST_DELAY_S = 1.0
MAX_RETRIES = 3
TIMEOUT_S = 90   # un escaneo mensual con filtro tarda ~5-20 s

logger = logging.getLogger("url_list_builder")

# /news/articles/2025-07-03/slug  |  /news/features/2025-07-03/slug
_ARTICLE_RE = re.compile(r"^/news/(articles|features)/(\d{4}-\d{2}-\d{2})/([^/?#]+)")

_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]


# ---------------------------------------------------------------------------
# Funciones puras (testeables sin red)
# ---------------------------------------------------------------------------
def slugify_company(nombre: str) -> str:
    """'Bank of America' -> 'bank-of-america'; 'Johnson & Johnson' -> 'johnson-johnson'.
    Asi aparecen los nombres en los slugs de Bloomberg."""
    s = nombre.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def canonicalize_url(raw: str) -> Optional[str]:
    """Normaliza una URL de archivo a su forma canonica en bloomberg.com.
    Devuelve None si no es un path de /news/ de bloomberg.com."""
    try:
        parts = urlsplit(raw.strip())
    except ValueError:
        return None
    host = parts.netloc.lower().split(":")[0]
    if not (host == "bloomberg.com" or host.endswith(".bloomberg.com")):
        return None
    path = parts.path
    if not path.startswith("/news/"):
        return None
    # algunas capturas archivadas traen whitespace URL-encoded al final del
    # slug (p.ej. ...%0a); se recorta para no duplicar ni romper el goto
    path = re.sub(r"(?:%0[ad9]|\s)+$", "", path, flags=re.IGNORECASE)
    # sin query (utm, ref=, etc.), sin fragmento, sin slash final
    return "https://www.bloomberg.com" + path.rstrip("/")


def classify_url(url: str) -> tuple[Optional[str], str]:
    """(published_date ISO o None, excluded_reason). excluded_reason vacio =
    articulo procesable por el scraper."""
    path = urlsplit(url).path
    m = _ARTICLE_RE.match(path)
    if m:
        return m.group(2), ""
    if path.startswith("/news/videos/"):
        return _date_from_path(path), "video"
    if path.startswith("/news/audio/"):
        return _date_from_path(path), "audio"
    return _date_from_path(path), "no-articulo"


def _date_from_path(path: str) -> Optional[str]:
    m = re.search(r"/(\d{4}-\d{2}-\d{2})/", path)
    return m.group(1) if m else None


def display_date(iso: Optional[str]) -> str:
    """'2025-07-03' -> 'July 3, 2025' (formato que muestra Bloomberg)."""
    if not iso:
        return ""
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return ""
    return f"{_MONTHS[d.month - 1]} {d.day}, {d.year}"


def slug_to_headline(url: str) -> str:
    """Stand-in legible derivado del slug; el scraper prefiere el headline real
    de la pagina y solo cae a este si la pagina no lo da."""
    m = _ARTICLE_RE.match(urlsplit(url).path)
    slug = m.group(3) if m else urlsplit(url).path.rsplit("/", 1)[-1]
    words = [w for w in slug.split("-") if w]
    return " ".join(w.capitalize() for w in words)


def keyword_regex(keywords: list[str]) -> str:
    """Regex de filtro para la API CDX (match sobre la URL completa)."""
    alternation = "|".join(re.escape(k) for k in keywords)
    return f".*({alternation}).*"


def month_prefixes(start: date, end: date) -> list[str]:
    """Prefijos CDX mensuales para las secciones con fecha en el path.
    ['bloomberg.com/news/articles/2023-07*', ..., 'bloomberg.com/news/features/2026-07*']"""
    months: list[str] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        months.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return [
        f"bloomberg.com/news/{section}/{month}*"
        for section in URL_SECTIONS
        for month in months
    ]


@dataclass
class UrlRow:
    ticker: str
    headline: str
    url: str
    published_date_display: str
    excluded_reason: str


def build_rows(
    ticker: str,
    urls: Iterable[str],
    date_range: Optional[tuple[date, date]] = None,
) -> list[UrlRow]:
    """Canonicaliza, clasifica, filtra por fecha de publicacion (del path) y
    deduplica. Orden: fecha de publicacion ascendente, sin-fecha al final."""
    seen: set[str] = set()
    rows: list[UrlRow] = []
    for raw in urls:
        url = canonicalize_url(raw)
        if not url or url in seen:
            continue
        seen.add(url)
        pub_iso, excluded = classify_url(url)
        if date_range and pub_iso:
            d = date.fromisoformat(pub_iso)
            if not (date_range[0] <= d <= date_range[1]):
                continue
        rows.append(UrlRow(
            ticker=ticker,
            headline=slug_to_headline(url),
            url=url,
            published_date_display=display_date(pub_iso),
            excluded_reason=excluded,
        ))
    rows.sort(key=lambda r: (r.published_date_display == "", _sort_date(r), r.url))
    return rows


def _sort_date(r: UrlRow) -> str:
    m = re.search(r"/(\d{4}-\d{2}-\d{2})/", r.url)
    return m.group(1) if m else "9999-99-99"


def merge_into_csv(path: Path, rows: list[UrlRow]) -> tuple[int, int]:
    """Fusiona filas nuevas en el CSV (dedup por URL; las filas EXISTENTES
    ganan: pueden estar curadas a mano). Devuelve (nuevas, total)."""
    existing: list[dict] = []
    existing_urls: set[str] = set()
    if path.exists():
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                existing.append({c: (row.get(c) or "") for c in URL_LIST_COLUMNS})
                existing_urls.add(row.get("url", ""))
    new = [r for r in rows if r.url not in existing_urls]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=URL_LIST_COLUMNS)
        w.writeheader()
        for row in existing:
            w.writerow(row)
        for r in new:
            w.writerow(vars(r))
    return len(new), len(existing) + len(new)


# ---------------------------------------------------------------------------
# Wayback Machine CDX (red). `fetch` es inyectable para tests.
# ---------------------------------------------------------------------------
def _default_fetch(url: str, params: dict) -> requests.Response:
    last_exc: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        time.sleep(REQUEST_DELAY_S + random.uniform(0.0, 0.5))
        try:
            resp = requests.get(url, params=params, timeout=TIMEOUT_S,
                                headers={"User-Agent": "quantai2026-url-list-builder"})
            if resp.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
            return resp
        except Exception as e:  # noqa: BLE001 - reintento generico con backoff
            last_exc = e
            wait = 2.0 ** attempt
            logger.warning(f"request fallo ({e}); reintento {attempt}/{MAX_RETRIES} en {wait:.0f}s")
            time.sleep(wait)
    raise RuntimeError(f"request agoto reintentos: {last_exc}")


def fetch_wayback_urls(
    keywords: list[str],
    start: date,
    end: date,
    fetch: Callable = _default_fetch,
) -> tuple[list[str], list[str]]:
    """URLs de bloomberg.com/news/{articles,features}/ cuyo slug contiene
    alguna keyword, consultando la CDX API por prefijos mensuales.

    Devuelve (urls, prefijos_fallidos). Un prefijo que falla no aborta el
    resto: se reporta para re-correr (el merge al CSV es idempotente)."""
    urls: list[str] = []
    prefixes = month_prefixes(start, end)
    logger.info(f"wayback: {len(prefixes)} queries mensuales "
                f"({start.isoformat()} -> {end.isoformat()})")
    failed = _query_prefixes(prefixes, keywords, urls, fetch)
    if failed:
        # Los fallos observados en la practica (503/504/cuerpo no-JSON) son
        # transitorios del servidor: un segundo pase suele completarlos sin
        # tener que re-correr el comando entero.
        logger.info(f"segundo pase sobre {len(failed)} prefijos fallidos")
        failed = _query_prefixes(failed, keywords, urls, fetch)
    return urls, failed


def _query_prefixes(
    prefixes: list[str], keywords: list[str], urls: list[str], fetch: Callable
) -> list[str]:
    """Consulta cada prefijo y acumula URLs en `urls`. Devuelve los fallidos."""
    failed: list[str] = []
    for i, prefix in enumerate(prefixes, start=1):
        params = {
            "url": prefix,
            "output": "json",
            "fl": "original",
            "collapse": "urlkey",
            "filter": f"original:{keyword_regex(keywords)}",
        }
        try:
            resp = fetch(WAYBACK_CDX, params)
        except RuntimeError as e:
            logger.warning(f"[{i}/{len(prefixes)}] {prefix}: {e}; prefijo omitido")
            failed.append(prefix)
            continue
        if resp.status_code != 200:
            logger.warning(f"[{i}/{len(prefixes)}] {prefix}: HTTP {resp.status_code}; omitido")
            failed.append(prefix)
            continue
        try:
            data = resp.json()
        except ValueError:
            logger.warning(f"[{i}/{len(prefixes)}] {prefix}: cuerpo no-JSON; omitido")
            failed.append(prefix)
            continue
        # formato: [["original"], ["https://..."], ...] — primera fila es header
        found = [row[0] for row in data[1:] if row]
        if found:
            logger.info(f"[{i}/{len(prefixes)}] {prefix}: {len(found)} URLs")
        urls.extend(found)
    return failed


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def load_universe() -> dict[str, str]:
    if not UNIVERSE_CSV.exists():
        logger.error(f"No existe {UNIVERSE_CSV}")
        raise SystemExit(2)
    out: dict[str, str] = {}
    with open(UNIVERSE_CSV, newline="") as f:
        for row in csv.DictReader(f):
            out[row["ticker"].strip().upper()] = row["nombre"].strip()
    return out


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)s | %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)])
    p = argparse.ArgumentParser(
        description="Construye listas de URLs para el scraper desde la Wayback "
                    "Machine (archivo publico), sin tocar bloomberg.com"
    )
    p.add_argument("--tickers", required=True, help="tickers separados por comas")
    p.add_argument("--start-date", required=True,
                   help="inicio del rango de publicacion (YYYY-MM-DD)")
    p.add_argument("--end-date", required=True,
                   help="fin del rango de publicacion (YYYY-MM-DD)")
    p.add_argument("--keywords",
                   help="keywords extra para el slug, separadas por comas (se suman "
                        "a la derivada de universe.csv). Solo valido con UN ticker.")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = p.parse_args()

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    if args.keywords and len(tickers) != 1:
        p.error("--keywords solo tiene sentido con un unico ticker")
    start = date.fromisoformat(args.start_date)
    end = date.fromisoformat(args.end_date)
    if start > end:
        p.error("--start-date debe ser <= --end-date")

    universe = load_universe()
    missing = [t for t in tickers if t not in universe]
    if missing:
        logger.error(f"Tickers fuera de universe.csv: {', '.join(missing)}")
        return 1

    any_failed = False
    for ticker in tickers:
        keywords = [slugify_company(universe[ticker])]
        if args.keywords:
            keywords += [k.strip().lower() for k in args.keywords.split(",") if k.strip()]
        logger.info(f"[{ticker}] keywords: {keywords}")

        raw_urls, failed_prefixes = fetch_wayback_urls(keywords, start, end)
        rows = build_rows(ticker, raw_urls, (start, end))
        usable = sum(1 for r in rows if not r.excluded_reason)
        out_path = args.output_dir / f"{ticker}.csv"
        new, total = merge_into_csv(out_path, rows)
        logger.info(
            f"[{ticker}] {len(rows)} filas ({usable} procesables) -> {out_path} "
            f"(+{new} nuevas, {total} totales)"
        )
        if failed_prefixes:
            any_failed = True
            logger.warning(
                f"[{ticker}] {len(failed_prefixes)} meses fallaron: "
                f"{', '.join(failed_prefixes)}. Re-corre el mismo comando para "
                "completarlos (el merge es idempotente)."
            )
        if usable == 0:
            logger.warning(
                f"[{ticker}] 0 articulos procesables. Prueba --keywords con "
                "variantes del nombre (p.ej. marcas, abreviaturas)."
            )
    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(main())
