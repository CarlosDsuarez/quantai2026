"""
Universo final CONGELADO — ENTRELINHAS / Desafio Quant AI 2026

Estrategia: long-short cross-sectional market-neutral sobre el gap de sentimiento
entre titular y cuerpo de articulos de Bloomberg.

ESTADO: PROVISIONAL — la lista de abajo NO esta confirmada todavia.
Este archivo no se modifica una vez pasado FREEZE_DATE. Cualquier cambio
posterior es una decision ex-post y debe documentarse en docs/decision_universo.md.

FREEZE_DATE movido 2026-08-10 -> 2026-08-12 el 2026-08-09, por DATOS, no por
resultados: ese mismo dia se recolectaron por primera vez las URLs (ver
docs/decision_universo.md §10). El backtest NO se ha corrido; no existe ningun
resultado que pudiera haber motivado el cambio.

Estado tras la medicion: 11 de los 12 tickers quedan confirmados. Con la ventana
de agregacion K=5 dias habiles, la regla de participacion minima se cumple en el
98,7% de los rebalanceos (§10.6). El unico que no sobrevive es ABEV: 4 articulos
en 3 años, 1,9% de participacion — ausencia real de cobertura, no fallo de recall
(§10.8). Reemplazo propuesto: NU, pendiente de recolectar y confirmar.

--------------------------------------------------------------------------------
CRITERIO DE SELECCION (en orden de prioridad declarado)
--------------------------------------------------------------------------------
  (a) Densidad de cobertura noticiosa en Bloomberg — binding constraint real de
      la estrategia: un ticker sin articulos no genera senal, genera huecos.
  (b) Serie de precios completa y limpia (sin huecos > 5 dias habiles).
  (c) Balance geografico: 7 US + 5 ADRs brasilenos.

LIMITACION CONOCIDA — LEER ANTES DE CITAR ESTE ARCHIVO
  Al momento del congelamiento el criterio (a) NO pudo medirse: habia CERO URLs
  recolectadas para los 30 tickers del universo base (data/raw/articles/_url_lists/
  no existia). El criterio (b) resulto uniforme: los 30 tickers tienen series
  identicas (752 filas, 2023-07-17 -> 2026-07-15, sin NaN, max gap 1 dia habil),
  por lo que tampoco discrimina.

  En consecuencia la seleccion se apoyo en:
    - el balance geografico (c), que si es verificable, y
    - el volumen en dolares promedio diario (ADV) de config/universe.csv como
      PROXY DECLARADO de (a): mayor float y liquidez correlacionan con mayor
      cobertura de Bloomberg en ingles.

  El proxy es una hipotesis, no una medicion. Se declara aqui de forma ex-ante
  para que la eleccion del universo no pueda re-interpretarse despues del
  backtest. Ver docs/decision_universo.md, seccion "Limitacion del criterio (a)".

Fuente del universo base (30 tickers, no modificar): config/universe.csv
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Fechas
# --------------------------------------------------------------------------
FREEZE_DATE: str = "2026-08-12"   # movido desde 2026-08-10 el 2026-08-09 (ver cabecera)

# Decision inicial 2026-08-08; FREEZE_DATE es la fecha a partir de la cual el
# universo es inmutable para el backtest y el reporte de la competencia.
DECISION_DATE: str = "2026-08-08"

# Idioma de la senal, decidido 2026-08-09 ANTES del backtest: se aceptan
# articulos en ingles Y portugues. Motivo: el 88,7% de la cobertura Bloomberg de
# BBD y el 100% de la de ABEV esta en portugues; restringir a ingles dejaria la
# pata BR sin senal. Implica validar que el extractor LLM mide el gap
# titular/cuerpo de forma comparable en ambos idiomas — pendiente de Fase 2.
SIGNAL_LANGUAGES: tuple[str, ...] = ("en", "pt")


# --------------------------------------------------------------------------
# Universo final: 12 tickers (7 US + 5 BR)
# --------------------------------------------------------------------------
UNIVERSE_US: tuple[str, ...] = (
    "AAPL",   # Apple                — Tech
    "MSFT",   # Microsoft            — Tech
    "NVDA",   # Nvidia               — Tech
    "AMZN",   # Amazon               — Consumer Disc
    "TSLA",   # Tesla                — Consumer Disc
    "META",   # Meta                 — Tech
    "JPM",    # JPMorgan             — Financials
)

UNIVERSE_BR: tuple[str, ...] = (
    "PBR",    # Petrobras            — Energy-Oil&Gas
    "VALE",   # Vale                 — Materials-Mining
    "ITUB",   # Itau Unibanco        — Financials-Banking
    "BBD",    # Bradesco             — Financials-Banking
    "NU",     # Nubank               — Fintech  (entra 2026-08-09 en lugar de ABEV)
)

# ABEV descartado el 2026-08-09 con evidencia medida, ANTES del backtest:
# 4 articulos en 3 años, participacion 3/157 rebalanceos (1,9%) con K=5. No es
# fallo de recall — el re-scan con --keywords brahma,skol añadio 1 solo articulo.
# NU: 104 articulos, 42/157 (26,8%). El swap no mueve la regla global (155/157 en
# ambos casos: los 7 US ya la saturan) pero sube la pata BR con >=3 senales de
# 32/157 (20,4%) a 45/157 (28,7%). Ver docs/decision_universo.md §10.8.

UNIVERSE_FINAL: tuple[str, ...] = UNIVERSE_US + UNIVERSE_BR

MARKET: dict[str, str] = {t: "US" for t in UNIVERSE_US} | {t: "BR" for t in UNIVERSE_BR}


# --------------------------------------------------------------------------
# Costos de transaccion — brackets por liquidez
# --------------------------------------------------------------------------
# ADV = volumen en dolares promedio diario (M USD), de config/universe.csv.
#
# SUPUESTO DECLARADO: el repo no traia modelo de costos previo (verificado por
# grep sobre cost/bps/slippage/spread/comision). Los brackets de abajo son una
# parametrizacion ex-ante conservadora, no una calibracion empirica: agrupan
# half-spread + impacto + comision para tickets chicos relativos al ADV.
# Si mas adelante se calibran contra ejecucion real, el cambio debe registrarse
# en docs/decision_universo.md — no editar en silencio.
#
# Los valores son ONE-WAY (por lado, en basis points). Un roundtrip = 2x.
COST_BRACKETS_BPS: dict[str, float] = {
    "A_mega_liquid": 5.0,    # ADV > 5.000 M USD/dia
    "B_liquid": 8.0,         # ADV 1.000 - 5.000 M USD/dia
    "C_adr_medio": 15.0,     # ADV 100 - 1.000 M USD/dia
    "D_adr_bajo": 25.0,      # ADV < 100 M USD/dia
}

TICKER_COST_BRACKET: dict[str, str] = {
    # --- US ---
    "NVDA": "A_mega_liquid",   # ADV 32.452,6
    "TSLA": "A_mega_liquid",   # ADV 27.314,2
    "AAPL": "A_mega_liquid",   # ADV 11.988,0
    "MSFT": "A_mega_liquid",   # ADV 10.224,3
    "AMZN": "A_mega_liquid",   # ADV  8.881,6
    "META": "A_mega_liquid",   # ADV  8.595,4
    "JPM":  "B_liquid",        # ADV  2.162,5
    # --- BR (ADRs NYSE) ---
    "VALE": "C_adr_medio",     # ADV    320,4
    "PBR":  "C_adr_medio",     # ADV    261,6
    "ITUB": "C_adr_medio",     # ADV    133,6
    "NU":   "C_adr_medio",     # ADV    528,8
    "BBD":  "D_adr_bajo",      # ADV     84,5
}

# Costo one-way efectivo por ticker, en bps. Es el dict que consume el backtest.
TRANSACTION_COST_BPS: dict[str, float] = {
    ticker: COST_BRACKETS_BPS[bracket]
    for ticker, bracket in TICKER_COST_BRACKET.items()
}


# --------------------------------------------------------------------------
# Pesos
# --------------------------------------------------------------------------
# NO se congelan pesos estaticos por ticker: la cartera es long-short
# cross-sectional, y el peso de cada activo en cada rebalanceo lo determina el
# ranking de la senal (gap de sentimiento), no una asignacion fija.
#
# Lo que si se congela ex-ante es la CONVENCION de ponderacion, para que no se
# elija despues de ver resultados:
WEIGHTING_SCHEME: str = "equal_weight_within_leg"  # equiponderado dentro de cada pata
GROSS_EXPOSURE: float = 1.0        # |long| + |short| = 1.0
NET_EXPOSURE_TARGET: float = 0.0   # market-neutral por construccion

# Regla de participacion minima, pre-registrada el 2026-08-08 (antes del backtest).
# Implementacion: src/backtest/portfolio_construction.has_minimum_participation
MIN_ASSETS_PER_REBALANCE: int = 6

# --------------------------------------------------------------------------
# Ventana de agregacion de la senal — PRE-REGISTRADA 2026-08-09
# --------------------------------------------------------------------------
# En cada fecha de rebalanceo, un ticker tiene senal valida si tiene al menos un
# articulo en los ultimos K dias habiles (inclusive). K se fija AQUI, antes del
# backtest, porque es el parametro con mas capacidad de overfitting de todo el
# pipeline: con el universo congelado, mover K de 1 a 5 cambia el cumplimiento de
# MIN_ASSETS_PER_REBALANCE del 24,8% al 98,7% de los rebalanceos.
#
# K=5 se elige por razon ESTRUCTURAL, no por maximizar participacion:
#   - coincide con el horizonte corto declarado de la estrategia (5 dias habiles);
#   - K=10 da 100% de cumplimiento y aun asi se DESCARTA, porque una ventana de 10
#     dias solapa dos periodos de rebalanceo consecutivos y reciclaria la misma
#     noticia en dos decisiones independientes.
# Ver docs/decision_universo.md §10.7.
SIGNAL_AGGREGATION_WINDOW_DAYS: int = 5


# --------------------------------------------------------------------------
# Invariantes
# --------------------------------------------------------------------------
assert len(UNIVERSE_FINAL) == 12, "el universo congelado debe tener 12 tickers"
assert len(UNIVERSE_US) == 7 and len(UNIVERSE_BR) == 5, "balance geografico 7 US / 5 BR"
assert len(set(UNIVERSE_FINAL)) == 12, "hay tickers duplicados"
assert set(TRANSACTION_COST_BPS) == set(UNIVERSE_FINAL), (
    "TRANSACTION_COST_BPS debe cubrir exactamente el universo congelado"
)
assert MIN_ASSETS_PER_REBALANCE <= len(UNIVERSE_FINAL)
