# Contexto: scraper de Bloomberg + situación de cookies

Documento de handoff. Escrito para que otro agente/dev entre en frío y pueda colaborar
sin tener el historial de la conversación.

Fecha del snapshot: **2026-07-29** (addendum del 2026-07-30 en §10)
Repo: `/Users/carlos_suarez7/QUANT ITAU/quantai2026`
Archivo central: `src/ingestion/bloomberg_scraper.py`

---

## 0. TL;DR

- El scraper **ya fue reescrito**: pasó de automatizar búsqueda+paginación en la UI de
  Bloomberg a consumir una **lista de URLs pre-recolectada** desde un CSV.
- La reescritura está **verificada en seco** (fixtures locales, 4 rutas de parseo).
- **Bloqueado en dos frentes**:
  1. No existe todavía ninguna lista de URLs (`data/raw/articles/_url_lists/` no está creado).
  2. Las cookies exportadas caducan en **minutos**, no en días — ver §5. Ese es el
     problema estructural sin resolver.
- **Restricción no negociable**: no se evade la detección anti-bot de ninguna forma.
  Ver §7 antes de proponer soluciones.
- **2026-07-30:** los dos frentes tienen ya solución implementada — ver §10.

---

## 1. Qué es el proyecto

Pipeline de ingesta para el "Desafío Quant AI 2026". Fase 1 = recolectar artículos de
prensa financiera para un universo de 30 tickers, y de ahí extraer señal de sentimiento.

```
quantai2026/
├── config/
│   ├── universe.csv              # 30 tickers: ticker,nombre,pais,sector,vol_dolar_diario_prom_M
│   └── bloomberg_cookies.json    # cookies exportadas A MANO (ver §5)
├── data/raw/
│   ├── prices/                   # ya poblado (yfinance)
│   └── articles/
│       ├── _samples/             # dumps de debug + HTML de parseos fallidos
│       └── _url_lists/           # CSV de URLs (ver §10.2: ahora hay builder)
├── outputs/
│   ├── scraper_log.txt
│   └── parse_failures.log
└── src/
    ├── ingestion/bloomberg_scraper.py     # <-- este documento
    ├── ingestion/url_list_builder.py      # <-- nuevo (§10.2)
    ├── ingestion/price_fetcher.py
    ├── extraction/llm_sentiment_gap_extractor.py
    └── validation/coverage_report.py      # consume el parquet del scraper
```

Deps en `.venv`: `playwright>=1.44`, `pandas>=2.0`, `pyarrow>=15.0`, `anthropic`,
`beautifulsoup4`, `lxml`, `tenacity`, `requests`. Tests: `pytest` (ver §10.5).

---

## 2. Arquitectura vieja y por qué murió

El diseño original hacía, por cada ticker:

1. Navegar a `https://www.bloomberg.com/search?query={empresa}`
2. Abrir el modal de rango de fechas y fijar `#start-date-input` / `#end-date-input`
3. Hacer clic en "Load more" en bucle hasta agotar resultados
4. Recolectar los `href` de los resultados
5. Abrir cada artículo y parsearlo

**Falló en los pasos 2–3.** Dos síntomas encadenados:

**(a) El modal de fechas es imposible de clickear de forma confiable.** Su propio
backdrop intercepta los eventos de puntero mientras se reposiciona. Extracto literal de
`outputs/scraper_log.txt`:

```
- <div class="CustomDateRangeModal_backdrop__kwT1u">…</div> from
  <div id=":ra:" data-floating-ui-portal="">…</div> subtree intercepts pointer events
- retrying click action
  - waiting 500ms
  - waiting for element to be visible, enabled and stable
  - element is visible, enabled and stable
  - scrolling into view if needed
  - done scrolling
- <div class="CustomDateRangeModal_backdrop__kwT1u">…</div> ... intercepts pointer events
- retrying click action
...
2026-07-28 13:12:03,783 | ERROR | TICKERS FALLIDOS: PBR
```

Se intentó saltar el hit-testing de Playwright asignando el valor por JS directo
(`el.value = val` + `dispatchEvent('input'/'change')`). No alcanzó.

**(b) Automatizar búsqueda+paginación dispara el anti-bot (PerimeterX / HUMAN).**
Después del loop de reintentos, el sitio devuelve la pantalla de desafío. El mismo sitio
carga **sin fricción** en una sesión de navegador normal, no automatizada.

**Dato importante y contraintuitivo:** el 2026-07-28 se probó recolectar las URLs desde
Chrome real vía la extensión de Claude (o sea, no Playwright, sino el navegador del
usuario con su sesión logueada). **También disparó el desafío**, en la primera
navegación:

```
title: "Bloomberg - Are you a robot?"
"We've detected unusual activity from your computer network
 To continue, please click the box below to let us know you're not a robot."
Block reference ID: 2f701cb0-8abb-11f1-9c3d-ffb605817d30
resultLinks: 0
```

Conclusión: el trigger **no es Playwright en sí, es cualquier pestaña controlada por
CDP** (Chrome DevTools Protocol). Un navegador manejado programáticamente se detecta
aunque sea el Chrome real del usuario con cookies válidas. Esto descarta la extensión de
Chrome como workaround, que era la hipótesis de trabajo.

---

## 3. Arquitectura nueva (ya implementada)

El script **ya no busca, no pagina y no toca el modal de fechas.** Recibe una lista de
URLs recolectada con un navegador real (o con el builder de §10.2) y solo abre artículos
individuales — una interacción mucho menos "sospechosa" y sin ningún clic en la UI de
búsqueda.

### Entrada: `--url-list <path>` (ahora es la forma primaria de invocación)

CSV con estas 5 columnas obligatorias:

```csv
ticker,headline,url,published_date_display,excluded_reason
PBR,"Titular tal cual aparece",https://www.bloomberg.com/news/articles/2025-07-03/xxxx,"July 3, 2025",
PBR,"Un clip de video",https://www.bloomberg.com/news/videos/2025-07-05/yyyy,"July 5, 2025",video
```

- `excluded_reason` **vacío** = fila a procesar. **No vacío** (`video`, `audio`, …) = se
  ignora. Convención de nombre: `data/raw/articles/_url_lists/{TICKER}.csv`.
- `published_date_display` es la fecha tal cual la muestra Bloomberg, en inglés
  (`Month D, YYYY`). Se usa solo como fallback (ver abajo).
- `load_url_list()` también descarta filas sin URL y deduplica por URL.

`--tickers` sobrevive pero cambió de significado: ahora **filtra filas dentro del CSV**,
ya no dispara ninguna búsqueda.

### Salida: `data/raw/articles/{ticker}.parquet`

| columna | qué es |
|---|---|
| `ticker`, `company` | `company` se resuelve desde `config/universe.csv` por ticker |
| `headline`, `body` | extraídos de la página; `headline` cae al del CSV si la página no lo da |
| `published_at` | ISO-8601 UTC, ej. `2025-07-03T14:22:00+00:00` |
| `url`, `scraped_at` | — |
| `parse_method` | `json` \| `dom_fallback` — qué ruta logró extraer el cuerpo |
| `date_source` | `page` \| `url_list` \| `none` — de dónde salió `published_at` |

Idempotente: dedup por URL, guardado incremental cada 15 artículos, corridas reanudables.

`src/validation/coverage_report.py` consume este parquet (solo lee `published_at` y
`url`), así que el esquema debe seguir compatible hacia atrás.

### Reglas de calidad y de fecha

- **Umbral de confianza**: `MIN_BODY_CHARS = 100`. Ruta JSON primero; si su cuerpo no
  supera el umbral, se intenta el DOM; si **ninguna** lo supera, el artículo **NO se
  agrega y NO se marca como procesado** — el HTML crudo va a
  `_samples/failed_parse_{ticker}_{n}.html` y la URL a `outputs/parse_failures.log`,
  para poder auditar y reintentar.
- **Fecha**: se prefiere la parseada de la página (`date_source="page"`); si no hay, se
  normaliza `published_date_display` del CSV (`date_source="url_list"`); si tampoco,
  `published_at` queda vacío (`date_source="none"`).
- **Filtro de rango de fechas apagado por defecto**: `--start-date`/`--end-date` solo
  actúan si se pasan **ambos**. Las URLs vienen curadas; volver a filtrar solo
  podía descartar filas válidas en silencio.

### Qué se eliminó

`SEARCH_URL_TEMPLATE`, `apply_date_filter()`, `_js_set_input()`, `collect_search_urls()`,
`DEFAULT_MAX_RESULTS`, `DEFAULT_LOOKBACK_YEARS`. O sea: buscador, "Load more" y modal de
fechas, completos.

### Verificación hecha

Corrida en seco con fixtures `file://` locales (sin tocar bloomberg.com), 6 filas de CSV:

| fixture | resultado |
|---|---|
| payload `__NEXT_DATA__` con `datePublished` | `parse_method=json`, `date_source=page` |
| DOM + `<time datetime>`, sin JSON | `parse_method=dom_fallback`, `date_source=page` |
| DOM sin fecha | `dom_fallback`, `date_source=url_list` (`May 20, 2025` → `2025-05-20T00:00:00+00:00`) |
| cuerpo < 100 chars | `failed_parse_PBR_1.html` + log, **no** entra al parquet |
| fila con `excluded_reason=video` | ignorada |
| URL duplicada | descartada |

`6 filas totales -> 4 utilizables`, exit code 0. Las 4 rutas se comportan como se
especificó. (Formalizado como suite pytest en §10.5.)

---

## 4. Mapa del archivo actual

`src/ingestion/bloomberg_scraper.py` (los números de línea cambiaron con §10; usar
búsqueda por símbolo):

| símbolo | rol |
|---|---|
| `MIN_BODY_CHARS = 100` | umbral de confianza del parseo |
| `SESSION_COOKIE`, `ANTIBOT_COOKIES` | cookies críticas del pre-flight (§10.1) |
| `MAX_CONSECUTIVE_PAYWALLS = 3` | umbral sesión-caída (§10.3) |
| `URL_LIST_COLUMNS` | columnas obligatorias del CSV |
| `CHALLENGE_MARKERS` | strings que delatan la pantalla anti-bot |
| `PAYWALL_MARKERS` | strings que delatan paywall/sesión caída |
| `class Article` | dataclass → fila del parquet |
| `class SessionExpiredError` | aborta la corrida (exit 2) |
| `class PaywallDetected` | paywall en UNA página (§10.3) |
| `load_cookies_for_playwright()` | Cookie-Editor JSON → formato Playwright |
| `check_session_cookies()` | pre-flight fail-fast (§10.1) |
| `class RateLimiter` | 2.5s + jitter U(0, 1.5); exige `min_delay >= 2.0` |
| `detect_challenge()` | detecta, **no** resuelve |
| `pause_for_manual_solve()` | pausa en `input()` esperando al humano |
| `safe_goto()` | rate-limit → goto → detect challenge → check paywall |
| `_extract_next_data()`, `_find_article_subtree()`, `_walk_collect_text()`, `_dom_fallback()`, `_normalize_ts()`, `extract_article()` | parser JSON + fallback DOM |
| `load_url_list()` | lee y filtra el CSV |
| `load_company_map()` | ticker → nombre desde universe.csv |
| `_save_parse_failure()` | HTML crudo + log, sin marcar como procesado |
| `append_articles()` | parquet, dedup por URL |
| `scrape_url_list()` | loop principal por ticker |
| `main()` | CLI (ahora con `--profile-dir` / `--login`, §10.1) |

---

## 5. El problema de las cookies — esto es lo importante

### Cómo funciona hoy

El script **no** hace login y **no** renueva nada. Espera que el usuario:

1. Se loguee en bloomberg.com en su navegador normal.
2. Exporte las cookies con la extensión Cookie-Editor (**Export → JSON**).
3. Guarde ese JSON en `config/bloomberg_cookies.json`.

`load_cookies_for_playwright()` traduce ese formato al de Playwright (mapea `sameSite`,
`expirationDate` → `expires`, etc.) y las inyecta en el `BrowserContext` antes de
navegar. Ahora también **avisa al arrancar** si alguna viene ya vencida.

### Inventario actual (nombres y expiraciones; **valores nunca se documentan**)

20 cookies, exportadas el **2026-07-27 ~23:30**. Estado al 2026-07-29:

**Vencidas (4):**

| cookie | dominio | expiró | qué es |
|---|---|---|---|
| `octagon-jwtToken` | `www.bloomberg.com` | 2026-07-28 00:15 | **el token de sesión autenticada**. Sin esto no hay acceso de suscriptor |
| `_px2` | `.bloomberg.com` | 2026-07-27 23:44 | token de PerimeterX (anti-bot) |
| `_pxde` | `.bloomberg.com` | 2026-07-27 23:44 | datos de PerimeterX (anti-bot) |
| `__stripe_sid` | `.www.bloomberg.com` | 2026-07-28 00:00 | sesión de Stripe (billing) |

**Vivas (13):** `_pxvid` (2027-07-23), `_pxhd` (2027-07-27), `usnatUUID`,
`_session_id_backup` (2027-07-27), `_rdt_uuid`, `_rdt_pn`, `__gpi`, `__gads`, `__eoi`,
`_scor_uid`, `__stripe_mid`, `_sp_su`, `consentUUID`.

**De sesión, sin expiry (3):** `pxcts`, `_reg-csrf`, `_reg-csrf-token`.

### El problema real, y no es "se vencieron, exporta de nuevo"

Mira las ventanas de vida desde el momento de la exportación (23:30):

- `_px2` y `_pxde` murieron **~14 minutos** después.
- `octagon-jwtToken` murió **~45 minutos** después.

O sea: **la ventana útil de un export de cookies es de decenas de minutos, no de días.**
Y una corrida completa es larga por diseño: 2.5–4s por artículo × 86 artículos ≈ 5–6
minutos para un ticker, pero × 30 tickers son horas. El modelo "exporto cookies una vez y
corro el universo entero" **no es viable** con estos TTLs.

Peor: `_px2`/`_pxde` son precisamente las cookies de PerimeterX que acreditan que este
navegador ya pasó el control anti-bot. Si llegan vencidas o no se regeneran, la
probabilidad de que salte el desafío sube — que es exactamente lo que se observó.

### Qué se rompe y dónde, concretamente

En `safe_goto()`, después de cada `page.goto()`, se buscan los `PAYWALL_MARKERS`
(`"/subscription"`, `"sign in to continue"`, `"become a subscriber"`, `"start your free
trial"`). *(Nota: desde §10.3 un paywall puntual ya no aborta toda la corrida; ver
addendum.)* Con `octagon-jwtToken` vencido, esto va a saltar en el primer artículo.

Mitigante parcial: el guardado incremental cada 15 hace la corrida reanudable, así que
un corte no pierde el trabajo ya hecho.

Y si en vez del paywall salta el desafío anti-bot, `pause_for_manual_solve()` llama a
`input()` — o sea la corrida **requiere una terminal interactiva y una ventana de
navegador visible**, con un humano disponible para resolverlo a mano. No es
automatizable de punta a punta, por diseño.

---

## 6. Qué está bloqueado ahora mismo

1. **No existe ninguna lista de URLs.** `data/raw/articles/_url_lists/` no está creado.
   Sin el CSV, `load_url_list()` sale con `SystemExit(2)`. El plan original asumía un
   `PBR.csv` con 86 filas utilizables (4 excluidas por video/audio) que resultó no
   existir en disco — se buscó en todo `~`, no está en ninguna parte.
   → **Resuelto en §10.2** (builder desde la Wayback Machine).
2. **Recolectar ese CSV con navegador dirigido programáticamente no funciona** — el
   desafío anti-bot salta también ahí (§2, punto b). Hoy la única vía comprobada es que
   un humano copie los resultados a mano desde una ventana normal.
   → **Resuelto en §10.2** sin navegador: la fuente es un archivo público de terceros.
3. **Las cookies caducan en minutos** (§5). Aunque el CSV existiera, la corrida larga es
   frágil. → **Mitigado en §10.1** (perfil persistente + pre-flight fail-fast).

---

## 7. Restricción no negociable — leer antes de proponer soluciones

**No se evade la detección anti-bot de ninguna forma.** Esto no es una preferencia de
estilo, es una restricción de diseño del proyecto, escrita en el docstring del módulo.

Quedan **fuera de la mesa**:

- Resolver o pasar por encima del CAPTCHA / "Press & Hold" (ni a mano vía código, ni con
  servicios de resolución, ni simulando el gesto).
- Stealth plugins, parcheo de `navigator.webdriver`, spoofing de fingerprint,
  `undetected-chromedriver` y equivalentes.
- Rotación de proxies o IPs para diluir la detección.
- Rotar/falsificar user-agents para parecer otro cliente.
- Bajar el rate-limiting por debajo de 2.5s (el `RateLimiter` lo rechaza en el
  constructor a propósito: `min_delay >= 2.0`).

Lo que **sí** es aceptable: detectar el desafío, pausar, y pedir que un humano lo
resuelva en una ventana visible. Eso es lo que hace `pause_for_manual_solve()` hoy.

---

## 8. En qué queremos colaboración

Ordenado por valor (estado al 2026-07-30 entre corchetes):

1. **El TTL de las cookies (§5).** [→ §10.1: perfil persistente + pre-flight] ¿Hay una
   forma legítima de mantener la sesión viva durante una corrida larga?
2. **Recolectar las listas de URLs sin navegador automatizado.** [→ §10.2: Wayback
   Machine] Escalar a 30 tickers copiando a mano es caro; era el cuello de botella real.
3. **Robustez de `PAYWALL_MARKERS`.** [→ §10.3: contador de consecutivos] Distinguir
   "artículo puntual con paywall" (saltar y seguir) de "sesión caída" (abortar).
4. **Confirmar el esquema real del JSON de Bloomberg.** [pendiente: requiere una corrida
   real] La primera vez que se parsee un artículo real, el JSON crudo se vuelca a
   `_samples/first_article_next_data.json`; con eso se puede endurecer el parser.
5. **Tests.** [→ §10.5: suite pytest, 51 tests]

---

## 9. Cómo reproducir / correr

```bash
cd "/Users/carlos_suarez7/QUANT ITAU/quantai2026"

# 0. (una vez) bootstrap del perfil persistente — login 100% manual en la ventana:
.venv/bin/python src/ingestion/bloomberg_scraper.py --profile-dir config/browser_profile --login

# 1. construir la lista de URLs de PBR desde la Wayback Machine (sin tocar Bloomberg):
.venv/bin/python src/ingestion/url_list_builder.py --tickers PBR \
    --start-date 2023-07-01 --end-date 2026-07-15

# 2. smoke test de 3 artículos — terminal INTERACTIVA (input() puede pedir resolver desafío):
.venv/bin/python src/ingestion/bloomberg_scraper.py --profile-dir config/browser_profile \
    --url-list data/raw/articles/_url_lists/PBR.csv --max-articles 3

# 3. corrida completa de PBR:
.venv/bin/python src/ingestion/bloomberg_scraper.py --profile-dir config/browser_profile \
    --url-list data/raw/articles/_url_lists/PBR.csv
```

Inspeccionar resultados:

```bash
.venv/bin/python -c "import pandas as pd; d=pd.read_parquet('data/raw/articles/PBR.parquet'); print(len(d),'articulos'); print(d['parse_method'].value_counts()); print(d['date_source'].value_counts())"
ls data/raw/articles/_samples/failed_parse_PBR_*.html 2>/dev/null | wc -l
cat outputs/parse_failures.log
```

Métricas que se quieren reportar al final de la corrida de PBR: cuántos salieron por
`json`, cuántos cayeron en `dom_fallback`, cuántos fallaron por completo (con sus
`failed_parse_PBR_*.html`), y el desglose de `date_source`.

---

## 10. Addendum 2026-07-30 — qué se implementó

Colaboración sobre §8, respetando §7 en su totalidad (nada de lo implementado toca,
evade ni engaña al anti-bot).

### 10.1 Sesión: perfil persistente + pre-flight fail-fast (§8.1)

**Perfil persistente (`--profile-dir`, recomendado).** En vez de inyectar un snapshot
muerto de cookies, el scraper puede usar `launch_persistent_context` con un
`user_data_dir` en disco (sugerido: `config/browser_profile`, gitignored). El login lo
hace el humano UNA vez, a mano, en la ventana que abre `--login` (el script no toca el
formulario: abre la página y espera ENTER). A partir de ahí la sesión vive en el perfil
y **las cookies se refrescan solas durante la corrida** — el servidor renueva
`octagon-jwtToken`/`_px2`/`_pxde` en cada navegación, como en un navegador normal. Se
elimina el modelo "export con ventana útil de minutos". Env var: `BLOOMBERG_PROFILE_DIR`.
En este modo `--cookies-file` se ignora (inyectar un export viejo pisaría cookies
frescas del perfil).

*Advertencia honesta:* el navegador sigue siendo dirigido por CDP, así que el hallazgo
de §2(b) aplica — el desafío puede saltar igual. La diferencia es que (i) solo se abren
artículos individuales, (ii) si salta, se resuelve a mano y ese "aprobado" queda
persistido en `_px2`/`_pxde` del perfil, y (iii) el token de sesión ya no muere a los 45
minutos del export porque se renueva en uso.

**Pre-flight en modo cookies (`check_session_cookies()`).** Antes de abrir el navegador:
si `octagon-jwtToken` falta o ya venció → **exit 2 inmediato** con instrucciones (antes
el fallo aparecía recién en el primer artículo, con el navegador abierto). Si está vivo,
loguea cuántos minutos le quedan y a cuántos artículos alcanza al ritmo del rate-limit.
`_px2`/`_pxde` vencidas o ausentes → warning (no fatal: solo sube la probabilidad de
desafío). El modo cookies queda como legacy para corridas cortas.

### 10.2 Listas de URLs sin navegador: `src/ingestion/url_list_builder.py` (§8.2)

Nueva herramienta que construye los CSV de `_url_lists/` consultando la **CDX API de la
Wayback Machine** (web.archive.org) — un archivo público de terceros. **No dirige ningún
navegador y no toca bloomberg.com**, así que el anti-bot es irrelevante y no necesita
sesión.

- Los slugs de Bloomberg llevan el nombre de la empresa y la fecha de publicación en el
  path (`/news/articles/2025-07-03/petrobras-...`), así que se filtra server-side por
  keyword (derivada de `universe.csv`, extensible con `--keywords`) y se consulta por
  **prefijos mensuales** (una query sobre todo `/news/*` excede el timeout del servidor;
  verificado). Un mes que falla se reporta y no aborta; re-correr es idempotente (merge
  con dedup por URL, las filas existentes — posiblemente curadas a mano — ganan).
- Fuentes evaluadas y descartadas: **Common Crawl** (cero capturas de
  `bloomberg.com/news/articles/*` en índices de 2022–2025; Bloomberg bloquea CCBot —
  verificado empíricamente) y **GDELT** (indexa Bloomberg con titulares reales, pero su
  rate-limit público es agresivo; queda como opción a explorar).
- Limitación conocida: recall imperfecto — artículos cuyo slug no menciona la empresa no
  aparecen, y la Wayback Machine solo tiene lo que alguien archivó. Corrida real de
  validación (PBR, 2023-07-01 → 2026-07-15): **216 artículos únicos procesables**
  (41 de 2023, ~114 de 2024, 60 de 2025, 29 de 2026 antes de dedup) — más del doble de
  los 86 que asumía el plan original. Complementar a mano si la cobertura por mes es
  baja (el merge respeta lo manual). El CSV vive en `data/` (gitignored): regenerarlo
  es un solo comando (§9 paso 1).

### 10.3 Paywall robusto (§8.3)

`safe_goto()` ya no lanza `SessionExpiredError` directo: lanza `PaywallDetected` (por
página). `scrape_url_list()` lleva un contador de paywalls **consecutivos**: el artículo
paywalled se salta SIN marcarse como procesado (reintentable en la próxima corrida), y
solo al llegar a `MAX_CONSECUTIVE_PAYWALLS = 3` se concluye que la sesión murió, se
persiste el batch acumulado y se aborta con exit 2. Un éxito resetea el contador.

### 10.4 Esquema real del JSON (§8.4) — pendiente

Sin cambio: requiere la primera corrida real, que vuelca
`_samples/first_article_next_data.json`. Con ese dump se endurece
`_find_article_subtree()`/`_walk_collect_text()`.

### 10.5 Tests (§8.5)

Suite `tests/` con pytest (51 tests, sin red ni navegador): conversión y validación de
cookies, pre-flight (`check_session_cookies`), `load_url_list`, `_normalize_ts`, parser
JSON (`_find_article_subtree`/`_walk_collect_text`), política de paywall (aborta a los
3 consecutivos, resetea con éxito, lo saltado queda reintentable, parse fallido no se
marca como procesado), y el builder completo (slugify, canonicalización, clasificación,
fechas, merge que respeta curado manual, prefijos mensuales, tolerancia a fallos por
mes). Correr con: `python -m pytest tests/ -q`.
