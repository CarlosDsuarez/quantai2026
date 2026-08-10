"""
Filter URL Noise — Desafio Quant AI 2026 (Fase 1: curacion de listas)

El filtro server-side de url_list_builder.py hace SUBSTRING match sobre la URL
completa (keyword_regex construye `.*(kw).*`). Para keywords que son subcadena de
otras palabras, eso mete ruido:

    keyword 'vale' captura, ademas de vale-*:
      kering-to-buy-30-of-valentino-from-mayhoola-for-1-7-billion
      die-deutsche-bank-und-der-rivale-im-suden-funf-themen-des-tages
      amlo-se-adelanto-a-cambio-global-pero-prevalecera-su-legado
      uk-charges-four-with-fraud-in-patissere-valerie-scandal
      tether-says-reserves-held-in-cash-equivalents-are-highest-ever

Medido 2026-08-09: 135 de 258 filas de VALE (52%) eran ruido.

Este script re-filtra exigiendo que la keyword aparezca como PALABRA del slug
(delimitada por guion o por el inicio/fin del slug), que es como Bloomberg
construye sus slugs.

NO toca el scraper, el parser ni el modulo de sentimiento: solo re-escribe las
listas de URLs que aun no se han scrapeado.

IMPORTANTE — correr DESPUES del ultimo pase del builder. merge_into_csv respeta
las filas existentes pero vuelve a añadir las nuevas, asi que un re-scan
posterior re-introduce el ruido que este script quita.

Uso:
    python src/validation/filter_url_noise.py --tickers VALE --dry-run
    python src/validation/filter_url_noise.py --tickers VALE
"""

import argparse
import csv
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[2]
UNIVERSE_CSV = PROJECT_ROOT / "config" / "universe.csv"
URL_LISTS_DIR = PROJECT_ROOT / "data" / "raw" / "articles" / "_url_lists"

URL_LIST_COLUMNS = ["ticker", "headline", "url", "published_date_display", "excluded_reason"]


def slugify_company(nombre: str) -> str:
    """Misma normalizacion que url_list_builder.slugify_company."""
    return re.sub(r"[^a-z0-9]+", "-", nombre.lower()).strip("-")


def load_universe() -> dict[str, str]:
    with open(UNIVERSE_CSV, newline="") as f:
        return {r["ticker"].strip().upper(): r["nombre"].strip() for r in csv.DictReader(f)}


def word_regex(keywords: list[str]) -> re.Pattern:
    """Keyword como palabra del slug: delimitada por '-' o por inicio/fin."""
    alt = "|".join(re.escape(k) for k in keywords)
    return re.compile(rf"(^|-)({alt})(-|$)")


def slug_of(url: str) -> str:
    return urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]


def filter_rows(rows: list[dict], pattern: re.Pattern) -> tuple[list[dict], list[dict]]:
    """(conservadas, descartadas). Las filas con excluded_reason ya puesto se
    conservan tal cual: el scraper las ignora igual y quedan auditables."""
    keep, drop = [], []
    for r in rows:
        if (r.get("excluded_reason") or "").strip():
            keep.append(r)
        elif pattern.search(slug_of(r["url"])):
            keep.append(r)
        else:
            drop.append(r)
    return keep, drop


def main() -> int:
    p = argparse.ArgumentParser(description="Re-filtra listas de URLs exigiendo "
                                            "match de keyword como palabra del slug")
    p.add_argument("--tickers", required=True, help="tickers separados por comas")
    p.add_argument("--keywords", help="keywords extra, separadas por comas "
                                      "(se suman a la derivada de universe.csv)")
    p.add_argument("--dry-run", action="store_true",
                   help="reporta que se quitaria, sin escribir nada")
    p.add_argument("--url-lists-dir", type=Path, default=URL_LISTS_DIR)
    args = p.parse_args()

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    if args.keywords and len(tickers) != 1:
        p.error("--keywords solo tiene sentido con un unico ticker")

    universe = load_universe()
    rc = 0
    for ticker in tickers:
        path = args.url_lists_dir / f"{ticker}.csv"
        if not path.exists():
            print(f"[{ticker}] no existe {path}; omitido", file=sys.stderr)
            rc = 1
            continue
        if ticker not in universe:
            print(f"[{ticker}] fuera de universe.csv; omitido", file=sys.stderr)
            rc = 1
            continue

        keywords = [slugify_company(universe[ticker])]
        if args.keywords:
            keywords += [k.strip().lower() for k in args.keywords.split(",") if k.strip()]
        pattern = word_regex(keywords)

        with open(path, newline="") as f:
            rows = [{c: (r.get(c) or "") for c in URL_LIST_COLUMNS} for r in csv.DictReader(f)]
        keep, drop = filter_rows(rows, pattern)

        pct = 100.0 * len(drop) / len(rows) if rows else 0.0
        print(f"[{ticker}] keywords={keywords} | {len(rows)} filas -> "
              f"conserva {len(keep)}, descarta {len(drop)} ({pct:.1f}% ruido)")
        for r in drop[:10]:
            print(f"    - {slug_of(r['url'])[:88]}")
        if len(drop) > 10:
            print(f"    ... y {len(drop) - 10} mas")

        if args.dry_run:
            print(f"[{ticker}] dry-run: no se escribio nada")
            continue
        if not drop:
            continue

        backup = path.with_suffix(".csv.bak")
        shutil.copy2(path, backup)
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=URL_LIST_COLUMNS)
            w.writeheader()
            w.writerows(keep)
        print(f"[{ticker}] escrito {path} (backup en {backup.name})")
    return rc


if __name__ == "__main__":
    sys.exit(main())
