"""
Bloomberg Scraper — Desafio Quant AI 2026 (Fase 1: ingesta)

Playwright en modo NO headless (ventana visible), usando cookies de una sesion
YA autenticada exportadas MANUALMENTE por el usuario (config/bloomberg_cookies.json,
formato Cookie-Editor -> Export JSON).

ARQUITECTURA: lista de URLs pre-recolectada (NO busqueda automatizada).
El script NO busca, NO pagina y NO toca el modal de filtro de fecha. Automatizar
ese flujo disparaba un desafio anti-bot en loop; el mismo sitio carga sin friccion
en una sesion de navegador real. Las URLs se recolectan a mano (navegador real) y
se entregan en un CSV; aqui solo se abren articulos individuales, una interaccion
mucho menos "sospechosa".

LIMITES DE DISEÑO (no negociables):
- NO automatiza login ni evade deteccion de bots/CAPTCHA. Si aparece un desafio
  (p.ej. "Press & Hold" de PerimeterX/HUMAN), el script PAUSA, pide resolverlo
  MANUALMENTE en la ventana visible, y espera input() antes de continuar.
- Rate-limiting explicito entre acciones: SCRAPER_MIN_DELAY_SECONDS (default 2.5s)
  + jitter, para no sobrecargar el sitio.

FORMATO del CSV de entrada (--url-list), una fila por resultado:
    ticker,headline,url,published_date_display,excluded_reason
    PBR,"Titular del articulo",https://www.bloomberg.com/news/articles/xxx,"July 3, 2025",
    PBR,"Clip de video",https://www.bloomberg.com/news/videos/yyy,"July 5, 2025",video
Las filas con excluded_reason NO vacio se ignoran (video/audio/no-articulo).

FLUJO:
  1. Lee el CSV, descarta filas excluidas / sin URL, dedup por URL.
  2. Agrupa por ticker; resuelve `company` desde config/universe.csv.
  3. Por URL: abre el articulo, localiza el <script> con el payload JSON
     (Next.js __NEXT_DATA__ u otro), reconstruye el cuerpo concatenando nodos
     paragraph/text; fallback a extraccion del DOM. Extrae titular y timestamp.

Salida: data/raw/articles/{ticker}.parquet
    ticker, company, headline, body, published_at (UTC), url, scraped_at,
    parse_method, date_source
Idempotente: dedup por URL, corridas reanudables (guardado incremental cada 15).

Umbral de calidad: un articulo se agrega solo si el cuerpo reconstruido supera
MIN_BODY_CHARS (100 chars). Si NI el JSON NI el DOM lo alcanzan, NO se agrega ni
se marca como scrapeado: el HTML crudo va a _samples/failed_parse_{ticker}_{n}.html
y la URL a outputs/parse_failures.log (para auditar/reintentar).

Fecha: se prefiere la parseada de la pagina (date_source="page"); si no hay, se
usa published_date_display del CSV (date_source="url_list"); si tampoco,
published_at queda vacio (date_source="none").

Uso:
    python src/ingestion/bloomberg_scraper.py --url-list data/raw/articles/_url_lists/PBR.csv
    python src/ingestion/bloomberg_scraper.py --url-list lista.csv --tickers PBR,VALE
"""

import argparse
import json
import logging
import os
import random
import sys
import time
from dataclasses import dataclass, asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
from playwright.sync_api import Page, sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[2]
UNIVERSE_CSV = PROJECT_ROOT / "config" / "universe.csv"
ARTICLES_DIR = PROJECT_ROOT / "data" / "raw" / "articles"
SAMPLES_DIR = ARTICLES_DIR / "_samples"
URL_LISTS_DIR = ARTICLES_DIR / "_url_lists"
LOG_FILE = PROJECT_ROOT / "outputs" / "scraper_log.txt"
PARSE_FAILURES_LOG = PROJECT_ROOT / "outputs" / "parse_failures.log"

DEFAULT_COOKIES_FILE = PROJECT_ROOT / "config" / "bloomberg_cookies.json"
DEFAULT_MIN_DELAY = 2.5          # segundos; jitter agrega hasta +1.5s
MIN_BODY_CHARS = 100             # umbral de exito: cuerpo real > 100 chars; si no -> parse fallido
SAVE_EVERY = 15                  # guardado incremental -> corrida reanudable

# Columnas obligatorias del CSV de URLs.
URL_LIST_COLUMNS = ["ticker", "headline", "url", "published_date_display", "excluded_reason"]

# Marcadores de pantalla de desafio anti-bot. NO se evaden; solo se detectan
# para pausar y pedir resolucion manual.
CHALLENGE_MARKERS = [
    "press & hold",
    "press and hold",
    "are you a robot",
    "verify you are human",
    "verifying you are human",
    "px-captcha",
    "_px",
    "perimeterx",
    "please enable javascript and cookies",
]

# Marcadores de sesion expirada / paywall / muro de login.
PAYWALL_MARKERS = [
    "/subscription",
    "sign in to continue",
    "become a subscriber",
    "start your free trial",
]

logger = logging.getLogger("bloomberg_scraper")


# ---------------------------------------------------------------------------
# Modelo de datos
# ---------------------------------------------------------------------------
@dataclass
class Article:
    ticker: str
    company: str
    headline: str
    body: str
    published_at: Optional[str]  # ISO-8601 UTC, con hora si esta disponible
    url: str
    scraped_at: str
    parse_method: str  # "json" | "dom_fallback": ruta usada para extraer
    date_source: str   # "page" | "url_list" | "none": origen de published_at


class SessionExpiredError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Cookies exportadas manualmente -> formato Playwright
# ---------------------------------------------------------------------------
_SAMESITE_MAP = {
    "no_restriction": "None", "none": "None", "unspecified": "Lax",
    "lax": "Lax", "strict": "Strict",
}


def load_cookies_for_playwright(cookies_file: Path) -> list[dict]:
    """Convierte el JSON de Cookie-Editor al formato que espera Playwright.
    El script no genera ni renueva cookies; solo las carga."""
    if not cookies_file.exists():
        logger.error(
            f"No existe {cookies_file}.\n"
            "  Pasos: (1) logueate en bloomberg.com en tu navegador,\n"
            "  (2) exporta cookies con Cookie-Editor (Export -> JSON),\n"
            f"  (3) guarda el JSON en {cookies_file}."
        )
        raise SystemExit(2)

    with open(cookies_file) as f:
        raw = json.load(f)
    if not isinstance(raw, list) or not raw:
        logger.error(f"{cookies_file} no contiene una lista de cookies valida.")
        raise SystemExit(2)

    cookies = []
    expired = []
    now = time.time()
    for c in raw:
        if "name" not in c or "value" not in c:
            logger.warning(f"Cookie sin name/value ignorada: {c.get('name', '?')}")
            continue
        pc = {
            "name": c["name"],
            "value": c["value"],
            "domain": c.get("domain", ".bloomberg.com"),
            "path": c.get("path", "/"),
        }
        if c.get("secure") is not None:
            pc["secure"] = bool(c["secure"])
        if c.get("httpOnly") is not None:
            pc["httpOnly"] = bool(c["httpOnly"])
        ss = str(c.get("sameSite", "")).lower()
        if ss in _SAMESITE_MAP:
            pc["sameSite"] = _SAMESITE_MAP[ss]
        exp = c.get("expirationDate") or c.get("expires")
        if isinstance(exp, (int, float)) and exp > 0:
            pc["expires"] = float(exp)
            if exp < now:
                expired.append(c["name"])
        cookies.append(pc)
    logger.info(f"{len(cookies)} cookies cargadas desde {cookies_file}")
    if expired:
        logger.warning(
            f"{len(expired)} cookies YA EXPIRADAS: {', '.join(expired)}. "
            "Si aparece el paywall, re-exporta las cookies de una sesion activa."
        )
    return cookies


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------
class RateLimiter:
    """Espera min_delay + jitter U(0, 1.5) segundos entre acciones."""

    def __init__(self, min_delay: float):
        if min_delay < 2.0:
            raise ValueError("min_delay debe ser >= 2.0s para no sobrecargar el sitio")
        self.min_delay = min_delay
        self._last = 0.0

    def wait(self) -> None:
        target = self.min_delay + random.uniform(0.0, 1.5)
        elapsed = time.monotonic() - self._last
        if elapsed < target:
            time.sleep(target - elapsed)
        self._last = time.monotonic()


# ---------------------------------------------------------------------------
# Deteccion de desafio anti-bot -> pausa manual (SIN evasion)
# ---------------------------------------------------------------------------
def detect_challenge(page: Page) -> bool:
    try:
        body_text = (page.inner_text("body", timeout=3000) or "").lower()
    except Exception:
        body_text = ""
    if any(m in body_text for m in CHALLENGE_MARKERS):
        return True
    # PerimeterX suele inyectar un div/px-captcha aunque el texto no cargue
    if "px-captcha" in (page.content() or "").lower():
        return True
    return False


def pause_for_manual_solve(page: Page) -> None:
    """Pausa y espera que el usuario resuelva el desafio a mano en la ventana
    visible. NO automatiza ni simula la resolucion."""
    logger.warning("=" * 70)
    logger.warning("DESAFIO ANTI-BOT DETECTADO.")
    logger.warning(f"URL: {page.url}")
    logger.warning("Resuelvelo MANUALMENTE en la ventana del navegador visible.")
    logger.warning("El script NO lo automatiza ni lo evade — es un limite de diseño.")
    logger.warning("=" * 70)
    try:
        input(">>> Cuando la pagina cargue normal, presiona ENTER para continuar... ")
    except EOFError:
        logger.error("Sin terminal interactiva para input(). Correr en modo interactivo.")
        raise SessionExpiredError("Desafio anti-bot sin terminal para resolucion manual.")


def safe_goto(page: Page, url: str, limiter: RateLimiter) -> None:
    """Navega respetando rate-limit; pausa si hay desafio; aborta si hay paywall."""
    limiter.wait()
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    # reintenta hasta 2 veces tras resolucion manual del desafio
    for _ in range(3):
        if detect_challenge(page):
            pause_for_manual_solve(page)
            continue
        break
    low = (page.content() or "").lower()
    if any(m in low for m in PAYWALL_MARKERS):
        raise SessionExpiredError(
            f"Paywall/login detectado en {page.url}. Re-exporta cookies de una "
            "sesion autenticada valida y vuelve a correr."
        )


# ---------------------------------------------------------------------------
# Parseo de articulo: JSON (Next.js) primario + fallback DOM
# ---------------------------------------------------------------------------
_debug_dumped = False


def _extract_next_data(page: Page) -> Optional[dict]:
    """Devuelve el JSON de __NEXT_DATA__ u otro script de payload, si existe."""
    for sel in ('script#__NEXT_DATA__', 'script[type="application/json"]'):
        for handle in page.query_selector_all(sel):
            txt = handle.text_content()
            if not txt:
                continue
            try:
                data = json.loads(txt)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                return data
    return None


def _find_article_subtree(node, depth=0):
    """Busca recursivamente un dict que parezca el objeto-articulo (tiene una
    clave tipo headline/title y otra tipo body/content)."""
    if depth > 8:
        return None
    if isinstance(node, dict):
        keys = {k.lower() for k in node.keys()}
        has_title = keys & {"headline", "title", "seotitle", "name"}
        has_body = keys & {"body", "content", "components", "paragraphs", "articlebody"}
        if has_title and has_body:
            return node
        for v in node.values():
            found = _find_article_subtree(v, depth + 1)
            if found:
                return found
    elif isinstance(node, list):
        for x in node:
            found = _find_article_subtree(x, depth + 1)
            if found:
                return found
    return None


def _walk_collect_text(node, out: list, depth=0):
    """Concatena texto de nodos tipo paragraph/text del subarbol JSON."""
    if depth > 30:
        return
    if isinstance(node, dict):
        ntype = str(node.get("type", "")).lower()
        val = node.get("value")
        if ntype in ("text", "paragraph") and isinstance(val, str) and val.strip():
            out.append(val.strip())
        elif isinstance(val, str) and val.strip() and "content" not in node and "children" not in node:
            out.append(val.strip())
        for k in ("content", "children", "value", "data", "body", "paragraphs", "components"):
            v = node.get(k)
            if isinstance(v, (list, dict)):
                _walk_collect_text(v, out, depth + 1)
    elif isinstance(node, list):
        for x in node:
            _walk_collect_text(x, out, depth + 1)


def _dump_debug(data: dict) -> None:
    """Vuelca el JSON crudo del primer articulo para confirmar el esquema real."""
    global _debug_dumped
    if _debug_dumped:
        return
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    path = SAMPLES_DIR / "first_article_next_data.json"
    try:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False)[:2_000_000])
        logger.info(f"[debug] JSON del primer articulo volcado a {path} (confirmar esquema).")
    except Exception as e:
        logger.warning(f"[debug] no se pudo volcar JSON: {e}")
    _debug_dumped = True


def _dom_fallback(page: Page) -> tuple[str, str, Optional[str]]:
    """Extraccion desde el DOM renderizado si el JSON no alcanza."""
    headline = ""
    for sel in ("h1", '[data-component="headline"]', "header h1"):
        el = page.query_selector(sel)
        if el and el.inner_text().strip():
            headline = el.inner_text().strip()
            break
    paras = [p.inner_text().strip() for p in page.query_selector_all("article p, main p")]
    body = "\n\n".join(p for p in paras if p)
    published = None
    t = page.query_selector("time[datetime]")
    if t:
        published = t.get_attribute("datetime")
    return headline, body, published


def _normalize_ts(value) -> Optional[str]:
    if not value:
        return None
    try:
        s = str(value).strip()
        if not s:
            return None
        if s.isdigit():  # epoch (s o ms)
            v = int(s)
            v = v / 1000 if v > 1e12 else v
            return datetime.fromtimestamp(v, tz=timezone.utc).isoformat()
        dt = pd.to_datetime(s, utc=True, errors="coerce")
        return None if pd.isna(dt) else dt.isoformat()
    except Exception:
        return None


def extract_article(page: Page) -> tuple[str, str, Optional[str], Optional[str]]:
    """(headline, body, published_at ISO-8601 UTC, parse_method).

    parse_method: "json" si el walker de JSON supero el umbral MIN_BODY_CHARS;
    "dom_fallback" si lo supero la extraccion del DOM; None si NINGUNA ruta
    alcanzo el umbral (articulo no extraible -> no se marca como procesado).
    """
    json_head, json_body, json_pub = "", "", None
    data = _extract_next_data(page)
    if data:
        _dump_debug(data)
        subtree = _find_article_subtree(data) or data
        for k in ("headline", "title", "seoTitle", "name"):
            if isinstance(subtree.get(k), str):
                json_head = subtree[k].strip()
                break
        for k in ("datePublished", "publishedAt", "publishDate", "published", "pubDate"):
            if subtree.get(k):
                json_pub = _normalize_ts(subtree[k])
                break
        parts: list[str] = []
        _walk_collect_text(subtree, parts)
        json_body = "\n\n".join(dict.fromkeys(parts))  # dedup preservando orden

    # ruta principal: JSON si su cuerpo supera el umbral
    if len(json_body.strip()) > MIN_BODY_CHARS:
        return json_head, json_body, json_pub, "json"

    # fallback DOM
    dom_head, dom_body, dom_pub = _dom_fallback(page)
    if len(dom_body.strip()) > MIN_BODY_CHARS:
        return (json_head or dom_head), dom_body, (json_pub or _normalize_ts(dom_pub)), "dom_fallback"

    # ninguna ruta supero el umbral -> extraccion fallida (method=None)
    return (json_head or dom_head), "", (json_pub or _normalize_ts(dom_pub)), None


# ---------------------------------------------------------------------------
# Lista de URLs pre-recolectada (reemplaza busqueda + paginacion en la UI)
# ---------------------------------------------------------------------------
def load_url_list(path: Path) -> pd.DataFrame:
    """Lee el CSV de URLs recolectadas a mano y devuelve solo las filas usables.

    Descarta: filas con excluded_reason no vacio (video/audio/no-articulo),
    filas sin URL, y duplicados por URL (conserva la primera)."""
    if not path.exists():
        logger.error(
            f"No existe la lista de URLs {path}.\n"
            f"  Genera el CSV con columnas {', '.join(URL_LIST_COLUMNS)} y "
            f"guardalo (convencion: {URL_LISTS_DIR}/{{TICKER}}.csv)."
        )
        raise SystemExit(2)

    df = pd.read_csv(path, dtype=str).fillna("")
    missing = [c for c in URL_LIST_COLUMNS if c not in df.columns]
    if missing:
        logger.error(
            f"{path} no tiene las columnas obligatorias: {', '.join(missing)}. "
            f"Esperadas: {', '.join(URL_LIST_COLUMNS)}."
        )
        raise SystemExit(2)

    total = len(df)
    for col in URL_LIST_COLUMNS:
        df[col] = df[col].astype(str).str.strip()

    excluded = df[df["excluded_reason"] != ""]
    if not excluded.empty:
        by_reason = excluded["excluded_reason"].value_counts().to_dict()
        detail = ", ".join(f"{k}={v}" for k, v in by_reason.items())
        logger.info(f"{len(excluded)} filas excluidas por excluded_reason ({detail})")
    df = df[df["excluded_reason"] == ""]

    no_url = int((df["url"] == "").sum())
    if no_url:
        logger.warning(f"{no_url} filas sin URL descartadas")
    df = df[df["url"] != ""]

    before = len(df)
    df = df.drop_duplicates(subset="url", keep="first")
    if before != len(df):
        logger.warning(f"{before - len(df)} URLs duplicadas descartadas")

    df["ticker"] = df["ticker"].str.upper()
    logger.info(f"{path.name}: {total} filas totales -> {len(df)} utilizables")
    if df.empty:
        logger.error(f"{path} no dejo ninguna fila utilizable.")
        raise SystemExit(2)
    return df.reset_index(drop=True)


def load_company_map() -> dict[str, str]:
    """ticker -> nombre de empresa, desde config/universe.csv."""
    if not UNIVERSE_CSV.exists():
        logger.warning(f"No existe {UNIVERSE_CSV}; se usara el ticker como company.")
        return {}
    u = pd.read_csv(UNIVERSE_CSV)
    return {
        str(r["ticker"]).strip().upper(): str(r["nombre"]).strip()
        for _, r in u.iterrows()
    }


# ---------------------------------------------------------------------------
# Persistencia (parquet por ticker, dedup por URL, reanudable)
# ---------------------------------------------------------------------------
def _save_parse_failure(ticker: str, url: str, html: str) -> None:
    """Guarda el HTML crudo de un articulo no extraible y registra su URL.
    NO cuenta como scrapeado: no entra a la dedup/resumibilidad, para poder
    auditar/reintentar despues."""
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    PARSE_FAILURES_LOG.parent.mkdir(parents=True, exist_ok=True)
    n = len(list(SAMPLES_DIR.glob(f"failed_parse_{ticker}_*.html"))) + 1
    (SAMPLES_DIR / f"failed_parse_{ticker}_{n}.html").write_text(html)
    with open(PARSE_FAILURES_LOG, "a") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat()}\t{ticker}\t{url}\n")
    logger.warning(
        f"[{ticker}] parse fallido (<= {MIN_BODY_CHARS} chars). HTML -> "
        f"failed_parse_{ticker}_{n}.html; URL en parse_failures.log. NO marcado como scrapeado."
    )


def load_existing_urls(ticker: str) -> set[str]:
    path = ARTICLES_DIR / f"{ticker}.parquet"
    if not path.exists():
        return set()
    return set(pd.read_parquet(path, columns=["url"])["url"])


def append_articles(ticker: str, articles: list[Article]) -> None:
    if not articles:
        return
    path = ARTICLES_DIR / f"{ticker}.parquet"
    new_df = pd.DataFrame([asdict(a) for a in articles])
    if path.exists():
        combined = pd.concat([pd.read_parquet(path), new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset="url", keep="first")
    else:
        combined = new_df
    combined.to_parquet(path, index=False)
    logger.info(f"{ticker}: +{len(new_df)} nuevos, total {len(combined)}")


# ---------------------------------------------------------------------------
# Scrape de una lista de URLs (un ticker)
# ---------------------------------------------------------------------------
def scrape_url_list(
    page: Page, limiter: RateLimiter, ticker: str, company: str,
    rows: pd.DataFrame,
    date_range: Optional[tuple[date, date]] = None,
    max_articles: Optional[int] = None,
) -> int:
    """Abre cada URL de la lista y persiste los articulos extraibles.

    date_range: si se pasa, descarta articulos fuera del rango. None = sin
    filtro (las URLs ya vienen curadas)."""
    seen = load_existing_urls(ticker)
    todo = [r for r in rows.itertuples(index=False) if r.url not in seen]
    logger.info(
        f"[{ticker}] {len(rows)} URLs en lista, {len(rows) - len(todo)} ya en disco "
        f"-> {len(todo)} por procesar"
    )
    if max_articles is not None:
        todo = todo[:max_articles]

    batch: list[Article] = []
    added = 0
    for i, row in enumerate(todo, start=1):
        url = row.url
        logger.info(f"[{ticker}] {i}/{len(todo)} {url}")
        try:
            safe_goto(page, url, limiter)
            headline, body, published, method = extract_article(page)
        except SessionExpiredError:
            raise
        except Exception as e:
            logger.warning(f"[{ticker}] fallo articulo {url}: {e}")
            continue

        # extraccion fallida: ni JSON ni DOM superaron MIN_BODY_CHARS. NO se
        # marca como procesado (no entra a seen ni al parquet); se guarda el
        # HTML crudo y se registra la URL para auditar/reintentar.
        if method is None or len(body.strip()) <= MIN_BODY_CHARS:
            try:
                _save_parse_failure(ticker, url, page.content())
            except Exception as e:
                logger.warning(f"[{ticker}] no se pudo guardar HTML fallido {url}: {e}")
            continue

        # fecha: pagina primero, published_date_display del CSV como fallback
        if published:
            date_source = "page"
        else:
            published = _normalize_ts(row.published_date_display)
            date_source = "url_list" if published else "none"

        # filtro de rango opcional (solo si el usuario paso ambas fechas)
        if date_range and published:
            pub_d = pd.to_datetime(published, utc=True, errors="coerce")
            if pd.notna(pub_d) and not (date_range[0] <= pub_d.date() <= date_range[1]):
                logger.info(f"[{ticker}] fuera de rango ({pub_d.date()}), descartado: {url}")
                continue

        batch.append(Article(
            ticker=ticker, company=company,
            headline=headline or row.headline, body=body,
            published_at=published, url=url,
            scraped_at=datetime.now(timezone.utc).isoformat(),
            parse_method=method, date_source=date_source,
        ))
        seen.add(url)
        added += 1
        if len(batch) >= SAVE_EVERY:  # guardado incremental -> reanudable
            append_articles(ticker, batch)
            batch = []
    append_articles(ticker, batch)
    return added


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    ARTICLES_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(LOG_FILE)],
    )

    p = argparse.ArgumentParser(
        description="Scraper Bloomberg via Playwright sobre una lista de URLs pre-recolectada"
    )
    p.add_argument("--url-list", type=Path, required=True,
                   help=f"CSV con columnas {', '.join(URL_LIST_COLUMNS)}")
    p.add_argument("--tickers", help="subset separado por comas; filtra filas DENTRO del CSV")
    p.add_argument("--start-date", default=os.environ.get("SCRAPER_START_DATE"),
                   help="filtro client-side opcional; requiere tambien --end-date")
    p.add_argument("--end-date", default=os.environ.get("SCRAPER_END_DATE"),
                   help="filtro client-side opcional; requiere tambien --start-date")
    p.add_argument("--max-articles", type=int,
                   default=(int(os.environ["SCRAPER_MAX_ARTICLES"])
                            if os.environ.get("SCRAPER_MAX_ARTICLES") else None),
                   help="tope de articulos a procesar por ticker")
    p.add_argument("--cookies-file", type=Path,
                   default=Path(os.environ.get("BLOOMBERG_COOKIES_FILE", DEFAULT_COOKIES_FILE)))
    p.add_argument("--headless", action="store_true",
                   help="NO recomendado: sin ventana no puedes resolver desafios manualmente")
    args = p.parse_args()

    # Filtro de fechas DESACTIVADO salvo que se pasen ambas: las URLs vienen
    # curadas a mano, filtrar de nuevo solo puede descartar filas validas.
    date_range: Optional[tuple[date, date]] = None
    if args.start_date and args.end_date:
        date_range = (date.fromisoformat(args.start_date), date.fromisoformat(args.end_date))
        logger.info(f"Filtro client-side ACTIVO: {date_range[0]} -> {date_range[1]}")
    elif args.start_date or args.end_date:
        logger.warning("--start-date y --end-date deben ir juntos; filtro DESACTIVADO.")
    else:
        logger.info("Sin filtro de fechas (URLs pre-curadas).")

    url_df = load_url_list(args.url_list)
    if args.tickers:
        wanted = {t.strip().upper() for t in args.tickers.split(",")}
        url_df = url_df[url_df["ticker"].isin(wanted)]
        if url_df.empty:
            logger.error(f"Ningun ticker del CSV coincide con --tickers ({', '.join(wanted)}).")
            return 1

    company_map = load_company_map()
    cookies = load_cookies_for_playwright(args.cookies_file)
    min_delay = float(os.environ.get("SCRAPER_MIN_DELAY_SECONDS", DEFAULT_MIN_DELAY))
    limiter = RateLimiter(min_delay)

    failed: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=args.headless)
        context = browser.new_context()
        context.add_cookies(cookies)
        page = context.new_page()
        try:
            for ticker, rows in url_df.groupby("ticker", sort=False):
                company = company_map.get(ticker)
                if not company:
                    logger.warning(
                        f"[{ticker}] no esta en {UNIVERSE_CSV.name}; se usa el ticker como company"
                    )
                    company = ticker
                try:
                    n = scrape_url_list(
                        page, limiter, ticker, company, rows,
                        date_range=date_range, max_articles=args.max_articles,
                    )
                    logger.info(f"[{ticker}] listo: {n} articulos nuevos")
                except SessionExpiredError as e:
                    logger.error(str(e))
                    return 2
                except Exception as e:
                    logger.error(f"[{ticker}] FAIL: {e}")
                    failed.append(ticker)
        finally:
            context.close()
            browser.close()

    if failed:
        logger.error(f"TICKERS FALLIDOS: {', '.join(failed)}")
        return 1
    logger.info("Scraping completo sin fallos.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
