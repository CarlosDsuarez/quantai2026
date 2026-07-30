"""
Coverage Report — Desafio Quant AI 2026 (Fase 1: validacion de ingesta)

Lee los parquet de data/raw/articles/ y reporta por ticker:
  - cantidad de articulos
  - rango de fechas cubierto (min/max de published_at)
  - % de dias HABILES del rango con al menos un articulo

Sirve para decidir si el universo necesita ajustes por baja cobertura
mediatica antes de construir la señal (Fase 2).

Salida: tabla a consola + outputs/coverage_report.csv

Uso:
    python src/validation/coverage_report.py
"""

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
UNIVERSE_CSV = PROJECT_ROOT / "config" / "universe.csv"
ARTICLES_DIR = PROJECT_ROOT / "data" / "raw" / "articles"
OUTPUT_CSV = PROJECT_ROOT / "outputs" / "coverage_report.csv"


def coverage_for_ticker(ticker: str) -> dict:
    path = ARTICLES_DIR / f"{ticker}.parquet"
    base = {
        "ticker": ticker,
        "n_articles": 0,
        "date_min": None,
        "date_max": None,
        "pct_business_days_covered": 0.0,
    }
    if not path.exists():
        return base

    df = pd.read_parquet(path, columns=["published_at", "url"])
    df = df.drop_duplicates(subset="url")
    base["n_articles"] = len(df)

    dates = pd.to_datetime(df["published_at"], errors="coerce", utc=True).dropna()
    if dates.empty:
        return base  # hay articulos pero sin timestamp parseable

    days_with_article = set(dates.dt.date)
    d_min, d_max = min(days_with_article), max(days_with_article)
    business_days = pd.bdate_range(d_min, d_max).date

    covered = sum(1 for d in business_days if d in days_with_article)
    base["date_min"] = d_min.isoformat()
    base["date_max"] = d_max.isoformat()
    base["pct_business_days_covered"] = (
        round(100.0 * covered / len(business_days), 1) if len(business_days) else 0.0
    )
    return base


def main() -> int:
    if not UNIVERSE_CSV.exists():
        print(f"ERROR: no existe {UNIVERSE_CSV}", file=sys.stderr)
        return 1

    tickers = pd.read_csv(UNIVERSE_CSV)["ticker"].astype(str).str.strip().tolist()
    report = pd.DataFrame([coverage_for_ticker(t) for t in tickers])
    report = report.sort_values("pct_business_days_covered", ascending=False)

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    report.to_csv(OUTPUT_CSV, index=False)

    pd.set_option("display.max_rows", None, "display.width", 120)
    print(report.to_string(index=False))
    print(f"\nGuardado en {OUTPUT_CSV}")

    empty = report[report["n_articles"] == 0]["ticker"].tolist()
    if empty:
        print(f"\nAVISO: {len(empty)} tickers sin articulos: {', '.join(empty)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
