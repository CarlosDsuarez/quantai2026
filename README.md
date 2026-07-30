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
| `BLOOMBERG_PROFILE_DIR` | Perfil persistente de Chromium (recomendado, p.ej. `config/browser_profile`) |
| `BLOOMBERG_COOKIES_FILE` | Ruta al JSON de cookies exportadas manualmente (legacy, default `config/bloomberg_cookies.json`) |
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

### 2a. Listas de URLs (Wayback Machine, sin tocar Bloomberg)

El scraper consume una **lista de URLs pre-recolectada** por ticker
(`data/raw/articles/_url_lists/{TICKER}.csv`) — no busca ni pagina en la UI de
Bloomberg (automatizar la búsqueda disparaba el anti-bot en loop). El builder
construye esas listas consultando la CDX API de la Wayback Machine, un archivo
público de terceros: no dirige ningún navegador ni toca bloomberg.com.

```bash
python src/ingestion/url_list_builder.py --tickers PBR --start-date 2023-07-01 --end-date 2026-07-15
```

Flags: `--tickers` (coma-separados), `--keywords` (variantes del nombre para el
slug, solo con un ticker), `--output-dir`. Idempotente: re-correr fusiona por
URL y respeta filas curadas a mano. Recall imperfecto: solo aparecen artículos
cuyo slug menciona la empresa y que alguien archivó; complementar a mano si
hace falta (mismo CSV, el merge lo respeta).

### 2b. Scraper Bloomberg (Playwright, ventana visible)

Tecnología: **Playwright en modo NO headless** (navegador visible). Instalar el
browser una vez:

```bash
playwright install chromium
```

**Sesión (elegir un modo):**

- **Perfil persistente (`--profile-dir`, recomendado):** bootstrap una sola vez con

  ```bash
  python src/ingestion/bloomberg_scraper.py --profile-dir config/browser_profile --login
  ```

  Se abre una ventana; el login lo haces TÚ a mano; la sesión queda en el perfil
  (gitignored) y se refresca sola durante las corridas.
- **Cookies exportadas (legacy):** loguearse en bloomberg.com, exportar con
  *Cookie-Editor* → Export → JSON, guardar en `config/bloomberg_cookies.json`
  (gitignored). OJO: el token de sesión caduca en ~45 min desde el export, así
  que solo sirve para corridas cortas e inmediatas. Hay pre-flight: si el token
  ya venció, el scraper aborta ANTES de abrir el navegador con instrucciones.

El scraper **no** automatiza login ni evade detección de bots/CAPTCHA. Si aparece
un desafío (p.ej. "Press & Hold"), **pausa y pide resolverlo a mano** en la
ventana visible, espera ENTER, y continúa — nunca lo simula ni lo evade. Un
paywall puntual salta el artículo (queda reintentable); 3 paywalls consecutivos
= sesión caída y aborta (exit 2). Rate-limit: 2.5–4 s + jitter entre acciones.

```bash
# Probar primero solo con PBR (recomendado antes de escalar):
python src/ingestion/bloomberg_scraper.py --profile-dir config/browser_profile \
    --url-list data/raw/articles/_url_lists/PBR.csv --max-articles 3

# Corrida completa de un ticker:
python src/ingestion/bloomberg_scraper.py --profile-dir config/browser_profile \
    --url-list data/raw/articles/_url_lists/PBR.csv
```

Flags: `--url-list` (obligatorio salvo `--login`), `--tickers` (filtra filas del
CSV), `--start-date`/`--end-date` (filtro client-side, solo si van ambos),
`--max-articles`, `--profile-dir`/`--login`, `--cookies-file`, `--headless`
(desaconsejado: sin ventana no puedes resolver desafíos).

Salida: `data/raw/articles/{ticker}.parquet` con
`ticker, company, headline, body, published_at (UTC), url, scraped_at,
parse_method, date_source`. Idempotente (dedup por URL); una corrida
interrumpida se reanuda sola.

**Confirmación del parser en vivo:** la primera corrida real vuelca el JSON
crudo del primer artículo a
`data/raw/articles/_samples/first_article_next_data.json` para confirmar el
esquema y endurecer el parser si hace falta. Artículos cuyo cuerpo no supera el
umbral de calidad van a `_samples/failed_parse_*.html` + `outputs/parse_failures.log`
sin marcarse como procesados.

Contexto completo del scraper y la situación de cookies: `CONTEXTO_SCRAPER_COOKIES.md`.

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
| **1. Ingesta** | Scaffold, price fetcher, url_list_builder (Wayback), scraper (Playwright), validación de cobertura | ✅ hecho — **falta**: login manual del usuario en el perfil (`--login`) y primera corrida real para confirmar el esquema JSON del parser |
| **2. Señal** | Agregación intradía de sentimiento, construcción del gap por ticker/día, ranking cross-sectional | ⬜ pendiente (se define tras ver resultados de ingesta) |
| **3. Backtest** | Portafolio long-short market-neutral, horizontes 5/10 días, costos de transacción | ⬜ pendiente |
| **4. Análisis** | Métricas (Sharpe, drawdown, turnover), robustez, reporte final | ⬜ pendiente |

## Estructura

```
quantai2026/
├── config/universe.csv          # 30 tickers (no tocar)
├── config/browser_profile/      # perfil persistente de Chromium (gitignored)
├── config/bloomberg_cookies.json# cookies manuales legacy (gitignored)
├── data/raw/articles/           # parquet por ticker + _samples/ + _url_lists/
├── data/raw/prices/             # CSV por ticker
├── data/processed/              # fase 2+
├── src/ingestion/               # price_fetcher.py, url_list_builder.py, bloomberg_scraper.py
├── src/extraction/              # llm_sentiment_gap_extractor.py
├── src/validation/              # coverage_report.py
├── src/signal/  src/backtest/   # fase 2-3 (vacíos)
├── tests/                       # pytest (sin red ni navegador)
└── outputs/                     # logs y reportes
```

## Tests

```bash
python -m pytest tests/ -q
```
