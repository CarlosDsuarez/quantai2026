# Contexto: Scraper de Bloomberg y manejo de cookies

Documento de contexto para trabajar sobre `src/ingestion/bloomberg_scraper.py`.
Explica por qué el scraper depende de cookies exportadas manualmente, cómo se
cargan y convierten, qué pasa cuando expiran, y los límites de diseño que NO se
deben cambiar. Refleja el comportamiento real del código (el README describe un
flujo anterior de búsqueda + paginación que ya no existe — ver
[Historial de arquitectura](#historial-de-arquitectura)).

## Resumen en una línea

El scraper abre artículos individuales de Bloomberg con Playwright (Chromium,
ventana visible) usando cookies de una sesión que el usuario autenticó a mano;
nunca hace login, nunca evade anti-bot, y trabaja sobre una lista de URLs
pre-recolectada en un CSV.

## Por qué cookies manuales

- Bloomberg requiere suscripción (Digital) para leer el cuerpo de los
  artículos. La sesión autenticada vive en cookies del navegador.
- **Límite de diseño no negociable:** el script NO automatiza login, NO genera
  ni renueva cookies, y NO evade detección de bots ni CAPTCHAs. Solo *carga*
  cookies que el usuario exportó de una sesión real.
- Si aparece un desafío anti-bot (p.ej. "Press & Hold" de PerimeterX/HUMAN), el
  script **pausa** y pide resolverlo a mano en la ventana visible
  (`pause_for_manual_solve`, espera `input()`), hasta 2 reintentos por página.
  Por eso el modo headless está desaconsejado: sin ventana no hay forma de
  resolver el desafío.

## Flujo de exportación (manual, una vez por sesión)

1. Loguearse en bloomberg.com en un navegador normal.
2. Exportar cookies con la extensión **Cookie-Editor** → Export → **JSON**.
3. Guardar el JSON en `config/bloomberg_cookies.json` (ruta configurable con
   `BLOOMBERG_COOKIES_FILE` en `.env` o con `--cookies-file`).

El archivo está **gitignored** (ver `.gitignore`): son credenciales de sesión y
nunca deben commitearse.

Repetir la exportación cuando la sesión expire (el scraper lo indica — ver
[Expiración](#expiración-y-paywall)).

## Cómo el código carga las cookies

Función `load_cookies_for_playwright()` (`src/ingestion/bloomberg_scraper.py`):

- Valida que el archivo exista y contenga una **lista JSON no vacía**; si no,
  sale con código 2 e instrucciones de exportación.
- Convierte cada cookie del formato Cookie-Editor al que espera Playwright:
  - `name`/`value` obligatorios (cookies sin ellos se ignoran con warning).
  - Defaults: `domain=".bloomberg.com"`, `path="/"`.
  - `sameSite` se mapea: `no_restriction`/`none` → `None`,
    `unspecified` → `Lax`, `lax` → `Lax`, `strict` → `Strict`.
  - `expirationDate` (Cookie-Editor) o `expires` → `expires` (epoch float).
- **Detección de expiradas:** si alguna cookie ya venció al momento de cargar,
  se loguea un warning con sus nombres ("re-exporta las cookies de una sesión
  activa") pero **no aborta** — algunas cookies expiradas no impiden la sesión.
- Las cookies se inyectan una sola vez al `browser_context` de Playwright
  (`context.add_cookies(cookies)`) antes de abrir la primera página.

## Expiración y paywall

Detección en `safe_goto()` sobre el contenido de cada página:

- **Desafío anti-bot** (`CHALLENGE_MARKERS`: "press & hold", "are you a
  robot", "px-captcha", "perimeterx", etc.) → pausa manual, no aborta.
- **Paywall / muro de login** (`PAYWALL_MARKERS`: "/subscription", "sign in to
  continue", "become a subscriber", "start your free trial") → lanza
  `SessionExpiredError`, el proceso termina con código **2** pidiendo
  re-exportar cookies. No tiene sentido seguir: todas las páginas siguientes
  fallarían igual.

Como el guardado es incremental (cada 15 artículos) y hay dedup por URL, tras
re-exportar cookies basta relanzar el mismo comando: la corrida se reanuda sola
desde donde quedó.

## Arquitectura actual: lista de URLs pre-recolectada

El scraper **no busca ni pagina** en la UI de Bloomberg. Recibe un CSV
(`--url-list`, obligatorio) con URLs recolectadas a mano en un navegador real:

```
ticker,headline,url,published_date_display,excluded_reason
PBR,"Titular del articulo",https://www.bloomberg.com/news/articles/xxx,"July 3, 2025",
PBR,"Clip de video",https://www.bloomberg.com/news/videos/yyy,"July 5, 2025",video
```

- Filas con `excluded_reason` no vacío se ignoran (video/audio/no-artículo).
- Convención de ubicación: `data/raw/articles/_url_lists/{TICKER}.csv`.
- Solo se abren artículos individuales — una interacción mucho menos
  "sospechosa" para el anti-bot que automatizar búsqueda + paginación.

Por artículo: se intenta extraer del JSON embebido (`__NEXT_DATA__` u otro
`<script type="application/json">`); si el cuerpo no supera `MIN_BODY_CHARS`
(100), fallback a extracción del DOM; si tampoco, el HTML crudo va a
`data/raw/articles/_samples/failed_parse_{ticker}_{n}.html` y la URL a
`outputs/parse_failures.log`, **sin** marcarse como procesado (reintentable).

Salida: `data/raw/articles/{ticker}.parquet` con
`ticker, company, headline, body, published_at (UTC), url, scraped_at,
parse_method, date_source`.

## Rate limiting

`RateLimiter`: espera `SCRAPER_MIN_DELAY_SECONDS` (default 2.5 s, mínimo
forzado 2.0 s) + jitter uniforme de hasta 1.5 s entre navegaciones. El mínimo
está validado en el constructor: valores < 2.0 lanzan `ValueError`.

## Configuración relevante

| Variable / flag | Uso | Default |
|---|---|---|
| `BLOOMBERG_COOKIES_FILE` / `--cookies-file` | Ruta al JSON de cookies | `config/bloomberg_cookies.json` |
| `SCRAPER_MIN_DELAY_SECONDS` | Delay mínimo entre requests (≥ 2.0) | `2.5` |
| `--url-list` | CSV de URLs pre-recolectadas (**obligatorio**) | — |
| `--tickers` | Subset de tickers dentro del CSV | todos |
| `--start-date` / `--end-date` | Filtro client-side por `published_at`; solo activo si van **ambos** | desactivado |
| `--max-articles` (`SCRAPER_MAX_ARTICLES`) | Tope de artículos por ticker | sin tope |
| `--headless` | Sin ventana — desaconsejado (no se pueden resolver desafíos) | visible |

Comando típico:

```bash
python src/ingestion/bloomberg_scraper.py --url-list data/raw/articles/_url_lists/PBR.csv
```

## Historial de arquitectura

La versión descrita en el README (búsqueda en la UI, modal de filtro de fecha,
botón "Load more", flags `--max-results`) fue **descartada**: automatizar ese
flujo disparaba el desafío anti-bot en loop, mientras que el mismo sitio carga
sin fricción en una sesión de navegador real. La docstring del scraper es la
fuente de verdad sobre la arquitectura actual; el README quedó desactualizado
en la sección del scraper (los principios — cookies manuales, sin evasión,
rate-limit — siguen vigentes).

## Qué NO hacer al modificar el scraper

- No agregar login automatizado ni renovación de cookies.
- No agregar evasión/simulación de CAPTCHAs o "Press & Hold" (ni siquiera
  detrás de un flag).
- No bajar el rate-limit por debajo de 2.0 s.
- No reintroducir búsqueda/paginación automatizada en la UI de Bloomberg.
- No commitear `config/bloomberg_cookies.json` ni ningún archivo con cookies.
