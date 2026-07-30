# Quant AI 2026 — Sentiment Gap Strategy

Estrategia long-short cross-sectional market-neutral basada en la divergencia
entre el sentimiento del **cuerpo** de un artículo financiero (extraído por LLM)
y el sentimiento del **titular**. Hipótesis: subreacción de corto plazo por
atención limitada del mercado (Hirshleifer; Barber & Odean). Horizontes: 5 y 10
días hábiles.

- **Universo:** 30 tickers (18 US + 12 ADRs brasileños en NYSE), `config/universe.csv`. Seleccionado por liquidez ex-ante — no modificar.
- **Noticias:** Bloomberg.com (suscripción Digital, sesión autenticada manual).
- **Precios:** yfinance, OHLCV diario ajustado, lookback 3 años.

## Setup

```bash
cd quantai2026
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # y completar valores
```

Variables de entorno (ver `.env.example`):

| Variable | Uso |
|---|---|
| `ANTHROPIC_API_KEY` | Extractor LLM (`src/extraction/`) |
| `BLOOMBERG_COOKIES_FILE` | Ruta al JSON de cookies exportadas manualmente (default `config/bloomberg_cookies.json`) |
| `SCRAPER_MIN_DELAY_SECONDS` | Delay mínimo entre requests del scraper (≥ 2.0, default 2.5) |

## Cómo correr cada script

### 1. Precios (funcional)

```bash
python src/ingestion/price_fetcher.py
```

Descarga 3 años de OHLCV ajustado por ticker → `data/raw/prices/{ticker}.csv`
(`date,open,high,low,close,volume`). Log en consola y
`outputs/price_fetch_log.txt`. Exit code ≠ 0 si algún ticker falló — revisar el
resumen final, nunca falla en silencio.

### 2. Scraper Bloomberg (Playwright, ventana visible)

Tecnología: **Playwright en modo NO headless** (navegador visible), con cookies
de una sesión autenticada. Instalar el browser una vez:

```bash
playwright install chromium
```

**Requisito manual previo (una sola vez, y cuando expire la sesión):**

1. Loguearse en bloomberg.com en el navegador.
2. Exportar cookies con la extensión *Cookie-Editor* → Export → JSON.
3. Guardar en `config/bloomberg_cookies.json` (gitignored).

El scraper **no** automatiza login ni evade detección de bots/CAPTCHA. Si aparece
un desafío (p.ej. "Press & Hold"), **pausa y pide resolverlo a mano** en la
ventana visible, espera ENTER, y continúa — nunca lo simula ni lo evade. Si
detecta paywall/login aborta pidiendo re-exportar cookies. Rate-limit: 2.5–4 s
+ jitter entre acciones.

```bash
# Probar primero solo con PBR (recomendado antes de escalar):
python src/ingestion/bloomberg_scraper.py --tickers PBR --max-results 200

# Universo completo con rango de fechas explícito:
python src/ingestion/bloomberg_scraper.py --start-date 2023-07-01 --end-date 2026-07-15
```

Flags: `--tickers`, `--start-date`/`--end-date` (default: 3 años hasta hoy),
`--max-results` (tope por ticker, default 300), `--cookies-file`, `--headless`
(desaconsejado: sin ventana no puedes resolver desafíos).

Salida: `data/raw/articles/{ticker}.parquet` con
`ticker, company, headline, body, published_at (UTC), url, scraped_at`.
Idempotente (dedup por URL); una corrida interrumpida se reanuda sola. El rango
de fechas se garantiza filtrando client-side por `published_at` (independiente
del selector de la UI).

**Confirmación de selectores en vivo:** al no haber HTML de muestra guardado, los
selectores de UI (filtro de fecha, "Load more", enlaces) y el patrón exacto del
JSON de artículo se validan en la primera corrida real. Esa corrida vuelca el
JSON crudo del primer artículo a `data/raw/articles/_samples/first_article_next_data.json`
para confirmar el esquema y endurecer el parser si hace falta.

### 3. Validación de cobertura

```bash
python src/validation/coverage_report.py
```

Por ticker: nº de artículos, rango de fechas, % de días hábiles con ≥1
artículo. Tabla en consola + `outputs/coverage_report.csv`. Insumo para decidir
ajustes de universo por baja cobertura antes de la Fase 2.

### 4. Extractor LLM (existente, se integra en Fase 2)

`src/extraction/llm_sentiment_gap_extractor.py` — evalúa titular y cuerpo en
llamadas **aisladas** (sin contaminación mutua) y devuelve
`gap = body_sentiment − headline_sentiment`. Requiere `ANTHROPIC_API_KEY`.
Uso como módulo:

```python
from src.extraction.llm_sentiment_gap_extractor import extract_sentiment_gap
```

## Estado del pipeline

| Fase | Contenido | Estado |
|---|---|---|
| **1. Ingesta** | Scaffold, price fetcher, scraper (Playwright), validación de cobertura | ✅ hecho — **falta**: cookies de sesión del usuario para correr el scraper; selectores de UI se confirman en la primera corrida en vivo |
| **2. Señal** | Agregación intradía de sentimiento, construcción del gap por ticker/día, ranking cross-sectional | ⬜ pendiente (se define tras ver resultados de ingesta) |
| **3. Backtest** | Portafolio long-short market-neutral, horizontes 5/10 días, costos de transacción | ⬜ pendiente |
| **4. Análisis** | Métricas (Sharpe, drawdown, turnover), robustez, reporte final | ⬜ pendiente |

## Estructura

```
quantai2026/
├── config/universe.csv          # 30 tickers (no tocar)
├── config/bloomberg_cookies.json# cookies manuales (gitignored, crear tú)
├── data/raw/articles/           # parquet por ticker + _samples/ (HTML muestra)
├── data/raw/prices/             # CSV por ticker
├── data/processed/              # fase 2+
├── src/ingestion/               # price_fetcher.py, bloomberg_scraper.py
├── src/extraction/              # llm_sentiment_gap_extractor.py
├── src/validation/              # coverage_report.py
├── src/signal/  src/backtest/   # fase 2-3 (vacíos)
└── outputs/                     # logs y reportes
```
