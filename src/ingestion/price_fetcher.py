"""
Price Fetcher — Desafio Quant AI 2026 (Fase 1: ingesta)

Descarga OHLCV diario AJUSTADO (auto_adjust=True) de yfinance para todos los
tickers de config/universe.csv, ventana de 3 años, y guarda un CSV por ticker
en data/raw/prices/{ticker}.csv.

Manejo de fallos: cada ticker se descarga por separado; los fallos se loguean
explicitamente (consola + outputs/price_fetch_log.txt) y el script termina con
exit code != 0 si algun ticker no produjo datos. Nunca falla en silencio.

Uso:
    python src/ingestion/price_fetcher.py
"""

import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

PROJECT_ROOT = Path(__file__).resolve().parents[2]
UNIVERSE_CSV = PROJECT_ROOT / "config" / "universe.csv"
PRICES_DIR = PROJECT_ROOT / "data" / "raw" / "prices"
LOG_FILE = PROJECT_ROOT / "outputs" / "price_fetch_log.txt"

LOOKBACK_YEARS = 3
EXPECTED_COLUMNS = ["open", "high", "low", "close", "volume"]


def setup_logging() -> logging.Logger:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("price_fetcher")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    for handler in (logging.StreamHandler(sys.stdout), logging.FileHandler(LOG_FILE)):
        handler.setFormatter(fmt)
        logger.addHandler(handler)
    return logger


def load_universe() -> pd.DataFrame:
    if not UNIVERSE_CSV.exists():
        raise FileNotFoundError(f"No existe {UNIVERSE_CSV}")
    universe = pd.read_csv(UNIVERSE_CSV)
    if "ticker" not in universe.columns:
        raise ValueError(f"{UNIVERSE_CSV} no tiene columna 'ticker'")
    return universe


def fetch_ticker(ticker: str, start: date, end: date) -> pd.DataFrame:
    """Descarga OHLCV ajustado de un ticker. Lanza ValueError si viene vacio o corrupto."""
    df = yf.download(
        ticker,
        start=start.isoformat(),
        end=end.isoformat(),
        interval="1d",
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    if df is None or df.empty:
        raise ValueError("yfinance devolvio DataFrame vacio")

    # yfinance puede devolver columnas MultiIndex incluso para un solo ticker
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.rename(columns=str.lower)
    missing = [c for c in EXPECTED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"faltan columnas {missing} en respuesta de yfinance")

    df = df[EXPECTED_COLUMNS].copy()
    df.index.name = "date"
    df = df.reset_index()
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")

    if df["date"].duplicated().any():
        dups = df.loc[df["date"].duplicated(), "date"].tolist()
        raise ValueError(f"fechas duplicadas en respuesta: {dups[:5]}")
    if df[EXPECTED_COLUMNS].isna().all(axis=None):
        raise ValueError("todas las filas son NaN")
    return df


def main() -> int:
    logger = setup_logging()
    universe = load_universe()
    tickers = universe["ticker"].astype(str).str.strip().tolist()

    end = date.today()
    start = end - timedelta(days=365 * LOOKBACK_YEARS)
    PRICES_DIR.mkdir(parents=True, exist_ok=True)

    logger.info(f"Universo: {len(tickers)} tickers | ventana {start} -> {end}")

    ok, failed = [], []
    for ticker in tickers:
        try:
            df = fetch_ticker(ticker, start, end)
            out_path = PRICES_DIR / f"{ticker}.csv"
            df.to_csv(out_path, index=False)
            logger.info(f"OK   {ticker}: {len(df)} filas ({df['date'].iloc[0]} -> {df['date'].iloc[-1]})")
            ok.append(ticker)
        except Exception as e:
            logger.error(f"FAIL {ticker}: {e}")
            failed.append(ticker)

    logger.info("=" * 60)
    logger.info(f"RESUMEN: {len(ok)} ok / {len(failed)} fallidos de {len(tickers)}")
    if failed:
        logger.error(f"TICKERS FALLIDOS: {', '.join(failed)}")
        logger.error("Revisar antes de continuar — la ingesta NO esta completa.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
