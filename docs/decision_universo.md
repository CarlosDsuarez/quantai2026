# Decisión de universo — ENTRELINHAS / Desafio Quant AI 2026

**Estrategia:** long-short cross-sectional market-neutral sobre el *gap* de sentimiento
entre titular y cuerpo de artículos de Bloomberg. Horizontes 5 y 10 días hábiles.

| | |
|---|---|
| **Decisión inicial** | 2026-08-08 18:46 -05 |
| **`FREEZE_DATE`** | **2026-08-12** ⚠️ movido desde 2026-08-10 el 2026-08-09 — ver §10 |
| **Estado** | **PROVISIONAL.** La lista de §4 no está confirmada: la medición del 2026-08-09 la invalidó parcialmente |
| **Idioma de señal** | **EN + PT** (decidido 2026-08-09, ver §10.4) |
| **Artefacto congelado** | [`config/universe_final.py`](../config/universe_final.py) |
| **Universo base (no modificar)** | [`config/universe.csv`](../config/universe.csv) — 30 tickers |
| **Deadline competencia** | 2026-08-16 |

A partir de `FREEZE_DATE` el universo es inmutable. Cualquier cambio posterior es
una decisión **ex-post** y debe registrarse en este documento con fecha, motivo y
evidencia — no editando silenciosamente `universe_final.py`.

---

## 1. Criterio de selección declarado

En orden de prioridad, tal como se declaró antes de mirar los datos:

- **(a) Densidad de cobertura noticiosa en Bloomberg.** Es el *binding constraint*
  real de la estrategia: un ticker sin artículos no genera señal, genera huecos.
- **(b) Serie de precios completa y limpia**, sin huecos > 5 días hábiles.
- **(c) Balance geográfico:** 7 US + 5 ADRs brasileños.

---

## 2. ⚠️ Limitación del criterio (a) — leer antes de interpretar nada

**Al momento del congelamiento, el criterio (a) no pudo medirse.**

- `data/raw/articles/_url_lists/` **no existe**. `data/raw/articles/` contiene
  únicamente `_samples/`, vacío. `data/processed/` vacío. Cero parquets de artículos.
- **URLs recolectadas: 0, para los 30 tickers.** No es que algún ticker salga mal
  en cobertura — es que la métrica todavía no existe.
- `src/validation/coverage_report.py`, corrido hoy, devolvería `n_articles = 0` en
  las 30 filas.
- Causa: la última corrida del scraper (`outputs/scraper_log.txt`, 2026-07-30 09:00)
  quedó detenida esperando el login manual y nunca avanzó.

**Y el criterio (b) resultó no discriminante.** Auditados los 30 CSV de
`data/raw/prices/`:

```
distinct (n, start, end): [(752, '2023-07-17', '2026-07-15')]
tickers con gaps > 5 días hábiles: ninguno
tickers con NaN: ninguno
```

Los 30 son idénticos: 752 filas, gap máximo 1 día hábil (feriados), cero NaN.
Todos pasan (b) por igual.

### Qué se usó entonces

Con (a) vacío y (b) uniforme, la selección se apoyó en:

1. **El balance geográfico (c)**, que sí es verificable y se cumple exactamente (7/5).
2. **El ADV** (volumen en dólares promedio diario, columna
   `vol_dolar_diario_prom_M` de `universe.csv`) como **proxy declarado de (a)**:
   mayor float y liquidez correlacionan con mayor cobertura de Bloomberg en inglés.

**El proxy es una hipótesis, no una medición.** Se declara aquí de forma ex-ante,
antes de correr el backtest, para que la elección del universo no pueda
re-interpretarse después a conveniencia.

### Salvedad adicional sobre la ventana de precios

La serie arranca el **2023-07-17**, no en enero de 2023.
`src/ingestion/price_fetcher.py:29` usa `LOOKBACK_YEARS = 3` contado desde
`date.today()`, y se ejecutó el 2026-07-16. **No hay H1-2023.** Si el backtest
requiere 2023 completo, hay que re-correr el fetcher con `start` explícito.

---

## 3. Inventario completo (Tarea 1)

### Dónde está definido el universo

| Ruta | Rol |
|---|---|
| `config/universe.csv` | **Única fuente de verdad.** 30 filas: `ticker,nombre,pais,sector,vol_dolar_diario_prom_M` |
| `src/ingestion/price_fetcher.py:25` | `UNIVERSE_CSV` — itera los 30 para descargar OHLCV |
| `src/ingestion/url_list_builder.py:67` | `UNIVERSE_CSV` — deriva la keyword del slug desde `nombre` |
| `src/validation/coverage_report.py:24` | `UNIVERSE_CSV` — itera para el reporte de cobertura |
| `README.md:9` | Documenta "30 tickers (18 US + 12 ADRs BR) — no modificar" |

No hay ninguna lista de tickers *hardcodeada* en código: un solo punto de cambio.

### Tabla: ticker | mercado | URLs recolectadas | precio OK

Ordenada por mercado y ADV descendente. "Precio OK" = 752 filas, sin NaN,
sin huecos > 5 días hábiles.

| Ticker | Nombre | Mercado | Sector | URLs recolectadas | Precio OK | ADV $M | En universo final |
|---|---|---|---|---|---|---|---|
| NVDA | Nvidia | US | Tech | 0 | ✅ | 32 452,6 | ✅ |
| TSLA | Tesla | US | Consumer Disc | 0 | ✅ | 27 314,2 | ✅ |
| AAPL | Apple | US | Tech | 0 | ✅ | 11 988,0 | ✅ |
| MSFT | Microsoft | US | Tech | 0 | ✅ | 10 224,3 | ✅ |
| AMZN | Amazon | US | Consumer Disc | 0 | ✅ | 8 881,6 | ✅ |
| META | Meta | US | Tech | 0 | ✅ | 8 595,4 | ✅ |
| GOOGL | Alphabet | US | Tech | 0 | ✅ | 6 486,4 | ❌ |
| UNH | UnitedHealth | US | Healthcare | 0 | ✅ | 2 672,1 | ❌ |
| JPM | JPMorgan | US | Financials | 0 | ✅ | 2 162,5 | ✅ |
| XOM | Exxon Mobil | US | Energy | 0 | ✅ | 1 956,2 | ❌ |
| WMT | Walmart | US | Consumer Staples | 0 | ✅ | 1 694,8 | ❌ |
| BAC | Bank of America | US | Financials | 0 | ✅ | 1 619,9 | ❌ |
| BA | Boeing | US | Industrials | 0 | ✅ | 1 552,4 | ❌ |
| JNJ | Johnson & Johnson | US | Healthcare | 0 | ✅ | 1 540,9 | ❌ |
| CVX | Chevron | US | Energy | 0 | ✅ | 1 350,1 | ❌ |
| GS | Goldman Sachs | US | Financials | 0 | ✅ | 1 307,1 | ❌ |
| PFE | Pfizer | US | Healthcare | 0 | ✅ | 1 037,9 | ❌ |
| KO | Coca-Cola | US | Consumer Staples | 0 | ✅ | 1 003,1 | ❌ |
| NU | Nubank | BR | Fintech | 0 | ✅ | 528,8 | ❌ |
| VALE | Vale | BR | Materials-Mining | 0 | ✅ | 320,4 | ✅ |
| PBR | Petrobras | BR | Energy-Oil&Gas | 0 | ✅ | 261,6 | ✅ |
| ITUB | Itaú Unibanco | BR | Financials-Banking | 0 | ✅ | 133,6 | ✅ |
| BBD | Bradesco | BR | Financials-Banking | 0 | ✅ | 84,5 | ✅ |
| STNE | StoneCo | BR | Fintech | 0 | ✅ | 62,1 | ❌ |
| ABEV | Ambev | BR | Consumer Staples | 0 | ✅ | 58,3 | ✅ |
| GGB | Gerdau | BR | Materials-Steel | 0 | ✅ | 40,7 | ❌ |
| SBS | Sabesp | BR | Utilities | 0 | ✅ | 21,3 | ❌ |
| SUZ | Suzano | BR | Materials-PulpPaper | 0 | ✅ | 19,6 | ❌ |
| UGP | Ultrapar | BR | Energy-Logistics | 0 | ✅ | 7,6 | ❌ |
| TIMB | TIM Brasil | BR | Telecom | 0 | ✅ | 7,5 | ❌ |

**Totales:** 30 tickers · 0 URLs · 30/30 con precio OK · 12 seleccionados.

---

## 4. Los 12 seleccionados y por qué

### US (7)

| Ticker | Justificación |
|---|---|
| **NVDA** | ADV más alto del universo (32 453 $M). Cobertura Bloomberg saturada — el ticker con menor riesgo de hueco de señal. |
| **TSLA** | 27 314 $M. Alto flujo noticioso y alta dispersión titular/cuerpo: material ideal para la hipótesis del *gap*. |
| **AAPL** | 11 988 $M. Cobertura densa y continua, sin períodos muertos. |
| **MSFT** | 10 224 $M. Igual que AAPL; además aporta un segundo mega-cap Tech con perfil de noticia distinto (enterprise vs consumer). |
| **AMZN** | 8 882 $M. Consumer Disc, diversifica el sesgo Tech puro. |
| **META** | 8 595 $M. Alta frecuencia de noticia regulatoria/producto — buen generador de divergencia titular-cuerpo. |
| **JPM** | 2 163 $M. Único Financials US: ancla sectorial fuera de Tech/Consumer, indispensable para que las patas larga y corta no queden concentradas en un solo factor. |

### BR (5 — ADRs en NYSE)

| Ticker | Justificación |
|---|---|
| **VALE** | ADR brasileño más líquido del set elegido (320 $M) y con cobertura Bloomberg en inglés genuina (commodities, mineral de hierro, China). |
| **PBR** | 262 $M. Cobertura densa por petróleo + política brasileña. **Único ticker con scraper ya probado en vivo.** |
| **ITUB** | 134 $M. Mayor banco de Brasil, cobertura financiera regular. |
| **BBD** | 85 $M. Segundo banco; par natural de ITUB para *spread* intra-sectorial. |
| **ABEV** | 58 $M. Consumer Staples brasileño, diversifica frente al sesgo Energy/Materials/Financials del resto de la pata BR. **Es el más débil del set — ver §6.** |

### Balance resultante

- **Geográfico:** 7 US / 5 BR ✅ (criterio (c) cumplido exactamente).
- **Sectorial:** Tech 4, Consumer Disc 2, Financials 3, Energy 1, Materials 1, Staples 1.
- **Liquidez:** rango 58 $M – 32 453 $M. Los brackets de costos de transacción
  en `universe_final.py` absorben esta dispersión.

---

## 5. Descartados y por qué

### 5.1 Descartados previamente, antes de construir el universo de 30

Estos cuatro nunca entraron a `config/universe.csv`:

| Ticker | Nombre | Motivo del descarte |
|---|---|---|
| **ERJ** | Embraer | Liquidez del ADR insuficiente y cobertura Bloomberg concentrada en eventos puntuales (pedidos, ferias aeronáuticas). Genera señal a ráfagas, no un flujo continuo: incompatible con rebalanceo periódico. |
| **AZUL** | Azul | Situación de crédito/reestructuración durante la ventana. El precio queda dominado por *distress* idiosincrático, no por el contenido informativo del artículo. Contamina la hipótesis. |
| **BRFS** | BRF | Cobertura Bloomberg en inglés escasa y episódica. Volumen del ADR bajo. |
| **GOL** | Gol Linhas Aéreas | Igual que AZUL, en grado más severo: proceso de reestructuración y precio no representativo. |

Patrón común: los cuatro fallan el criterio (a) por cobertura episódica, y AZUL/GOL
fallan además por contaminación del retorno con eventos de crédito.

### 5.2 Descartados en esta selección (estaban en los 30, no entran a los 12)

Ninguno fue descartado por datos *malos* — todos tienen precio OK y 0 URLs, igual
que los seleccionados. Se descartan por **cupo** (12 plazas) y por el proxy de ADV
+ diversificación sectorial.

| Ticker | Mercado | ADV $M | Motivo |
|---|---|---|---|
| **GOOGL** | US | 6 486 | **El descarte más discutible.** ADV superior a JPM. Cae por concentración sectorial: con AAPL, MSFT, NVDA y META ya dentro, un quinto mega-cap Tech convertiría la pata US en una apuesta de factor, no en un *cross-section*. Primer candidato a entrar si se libera una plaza US. |
| UNH | US | 2 672 | Cupo. Healthcare aporta diversificación, pero su flujo de noticia es regulatorio y de baja frecuencia. |
| XOM, CVX | US | 1 956 / 1 350 | Cupo. Energy US redundante con PBR en la pata BR. |
| WMT, KO | US | 1 695 / 1 003 | Cupo. Staples de baja volatilidad idiosincrática: poco recorrido para que el *gap* se materialice en retorno. |
| BAC, GS | US | 1 620 / 1 307 | Cupo. Financials US ya cubierto por JPM, que los domina en ADV. |
| BA | US | 1 552 | Cupo. Industrials único, sin par sectorial. |
| JNJ, PFE | US | 1 541 / 1 038 | Cupo. Igual que UNH. |
| **NU** | BR | **529** | **Descarte cuestionado — ver §6.** ADV 9× el de ABEV. Se mantiene fuera solo por respetar la lista de candidatos de referencia. |
| STNE | BR | 62 | Cupo. Fintech BR; ADV comparable a ABEV pero sin el rol de diversificación sectorial (Staples) que aporta ABEV. |
| GGB | BR | 41 | Cupo + ADV bajo. Materials BR ya cubierto por VALE, que lo domina 8×. |
| SBS, SUZ | BR | 21 / 20 | ADV bajo. Cobertura Bloomberg en inglés escasa (utility local, pulpa). Alto riesgo de hueco de señal. |
| UGP, TIMB | BR | 7,6 / 7,5 | **Los dos ADV más bajos del universo.** Cobertura mínima y costo de transacción prohibitivo. Descarte claro incluso sin medir (a). |

---

## 6. Punto abierto: ABEV vs NU

Registrado **antes** de `FREEZE_DATE`, para que quede constancia de que se detectó
ex-ante y no después de ver resultados.

| | ABEV (dentro) | NU (fuera) |
|---|---|---|
| ADV $M/día | 58,3 | **528,8** (9,1×) |
| Precio OK | ✅ | ✅ |
| URLs medidas | 0 | 0 |
| Sector | Consumer Staples | Fintech |

**Bajo el proxy declarado (ADV → cobertura), NU domina a ABEV con holgura.**
Nubank tiene además cobertura Bloomberg en inglés estructuralmente más densa
(fintech de alto perfil, participación de Berkshire, expansión LatAm), mientras
que el flujo relevante de Ambev es mayormente local y en portugués — precisamente
el tipo de ticker que el criterio (a) debería filtrar.

**No se ejecutó el cambio** porque:

1. El proxy no es una medición, y la lista de candidatos de referencia incluía ABEV.
2. ABEV aporta la única exposición a Consumer Staples de la pata BR; NU duplicaría
   parcialmente el perfil financiero de ITUB/BBD.

**Ventana de decisión:** hasta `FREEZE_DATE` = 2026-08-10 hay margen para resolverlo
con datos reales. Basta correr el builder sobre ambos y comparar artículos procesables:

```bash
python src/ingestion/url_list_builder.py --tickers ABEV,NU --start-date 2023-07-17 --end-date 2026-07-15
```

Si `NU` supera a `ABEV` en artículos procesables por un margen material, el swap
queda justificado por evidencia y debe registrarse en este documento antes del
2026-08-10. Después de esa fecha, no.

---

## 7. Regra de participação mínima

> **Pré-registrada em 2026-08-08, ANTES de rodar qualquer backtest.**

Em cada data de rebalanceamento exige-se sinal válido em pelo menos **6 ativos**.
Se houver menos de 6 sinais válidos, a carteira permanece **neutra (sem posição)**
naquele período.

**Racional:** com poucos ativos, uma carteira long-short deixa de ser
market-neutral e passa a ser uma aposta idiossincrática. A regra é declarada
ex-ante para evitar decisões oportunistas durante o backtest.

### Parâmetros congelados

| Parâmetro | Valor | Onde |
|---|---|---|
| `min_assets` | **6** | `config/universe_final.py` → `MIN_ASSETS_PER_REBALANCE` |
| Ação se não cumprir | Carteira neutra (sem posição) | — |
| Universo | 12 tickers | `UNIVERSE_FINAL` |

O umbral de 6 é **metade do universo congelado** (6 de 12), o mínimo que ainda
permite 3 posições longas e 3 curtas equiponderadas.

### Definição de "sinal válido"

Um número real **finito**. São inválidos: `None`, `NaN`, `±inf`, e qualquer valor
não numérico. Booleanos são rejeitados explicitamente (em Python `bool` é subclasse
de `int`, e um `True` passando como sinal seria um bug silencioso).

### Implementação

Função pura, **ainda não cabeada** a nenhum pipeline — a Fase 3 não existe, e
cabeá-la pela metade convidaria a ajustá-la depois de ver resultados, que é
exatamente o que o pré-registro busca impedir.

[`src/backtest/portfolio_construction.py`](../src/backtest/portfolio_construction.py):

```python
def has_minimum_participation(signals_at_date, min_assets=6) -> bool
```

Aceita um mapping `ticker -> sinal` (conta tickers distintos com sinal válido) ou
uma sequência de sinais. Um `pandas.Series` entra pela via de mapping.

---

## 8. Costos de transacción congelados

Brackets por liquidez, **one-way en basis points** (roundtrip = 2×). Definidos en
`config/universe_final.py`.

| Bracket | ADV $M/día | bps | Tickers |
|---|---|---|---|
| `A_mega_liquid` | > 5 000 | 5,0 | NVDA, TSLA, AAPL, MSFT, AMZN, META |
| `B_liquid` | 1 000 – 5 000 | 8,0 | JPM |
| `C_adr_medio` | 100 – 1 000 | 15,0 | VALE, PBR, ITUB |
| `D_adr_bajo` | < 100 | 25,0 | BBD, ABEV |

**Supuesto declarado:** el repositorio no traía modelo de costos previo (verificado
por `grep` sobre `cost|bps|slippage|spread|comision|turnover`). Estos brackets son
una parametrización ex-ante conservadora, **no una calibración empírica**: agrupan
half-spread + impacto + comisión para tickets pequeños relativos al ADV. Si más
adelante se calibran contra ejecución real, el cambio debe registrarse aquí.

### Pesos

**No se congelan pesos estáticos por ticker.** La cartera es long-short
cross-sectional: el peso de cada activo en cada rebalanceo lo determina el ranking
de la señal, no una asignación fija. Lo que sí se congela ex-ante es la convención,
para que no se elija después de ver resultados:

| Constante | Valor |
|---|---|
| `WEIGHTING_SCHEME` | `equal_weight_within_leg` |
| `GROSS_EXPOSURE` | 1,0 |
| `NET_EXPOSURE_TARGET` | 0,0 (market-neutral por construcción) |

---

## 9. Registro de congelamiento

| | |
|---|---|
| **Fecha y hora de la decisión** | **2026-08-08 18:46:17 -05** |
| **`FREEZE_DATE` (vigencia)** | **2026-08-10** |
| Universo congelado | 12 tickers: AAPL, MSFT, NVDA, AMZN, TSLA, META, JPM, PBR, VALE, ITUB, BBD, ABEV |
| Regla de participación mínima | Pre-registrada, `min_assets = 6` |
| Artefactos | `config/universe_final.py`, `src/backtest/portfolio_construction.py`, este documento |
| Punto abierto con vencimiento 2026-08-10 | ABEV vs NU (§6) |

### Log de cambios posteriores a `FREEZE_DATE`

*(vacío — cualquier entrada aquí es una decisión ex-post y debe justificarse con
fecha, motivo y evidencia numérica)*

---

## 10. Medición del criterio (a) — 2026-08-09

**El criterio (a), declarado imposible de medir en §2, se midió el 2026-08-09** y
el resultado **invalida parcialmente la lista de §4**. Se registra aquí íntegro,
**antes de correr ningún backtest**.

Corrida del builder: 2026-08-08 18:56 → 2026-08-09 04:44 (**9 h 48 min**, no las
3,5 h estimadas — los HTTP 503/504 de la Wayback Machine triplicaron el tiempo por
reintentos). Ventana 2023-07-17 → 2026-07-15 (783 días hábiles).

### 10.1 Cobertura medida

| Ticker | URLs | Procesables | Días con artículo | % días hábiles | Art/mes |
|---|---|---|---|---|---|
| META | 1 882 | 1 881 | 673 | **86,0 %** | 52,2 |
| JPM | 1 731 | 1 731 | 672 | **85,8 %** | 48,1 |
| TSLA | 1 440 | 1 439 | 579 | 73,9 % | 40,0 |
| AMZN | 1 071 | 1 071 | 528 | 67,4 % | 29,8 |
| NVDA | 1 366 | 1 365 | 521 | 66,5 % | 37,9 |
| MSFT | 1 032 | 1 030 | 435 | 55,6 % | 28,6 |
| AAPL | 616 | 615 | 307 | 39,2 % | 17,1 |
| VALE | 258 | 258 | 179 | 22,9 % | 7,2 |
| PBR | 214 | 214 | 136 | 17,4 % | 5,9 |
| BBD | 53 | 53 | 41 | 5,2 % | 1,5 |
| ABEV | 3 | 3 | 2 | **0,3 %** | 0,1 |
| ITUB | 0 | 0 | 0 | **0,0 %** | 0,0 |

**Total 9 660 URLs procesables** (la estimación previa de ~4 800 se quedó a la
mitad). Reparto: **US 9 132 (94,5 %) · BR 528 (5,5 %)**.

Los conteos son **subestimaciones**: varios prefijos mensuales quedaron omitidos
tras agotar los 3 reintentos y el segundo pase (503/504). Re-correr es idempotente.

### 10.2 La regla de participación mínima no se cumple

Días hábiles con al menos N tickers con señal simultánea, sobre los 12 de §4:

| ≥N tickers | Días | % |
|---|---|---|
| ≥3 | 735 | 93,9 % |
| ≥5 | 478 | 61,0 % |
| **≥6 (la regla)** | **270** | **34,5 %** |
| ≥8 | 24 | 3,1 % |
| ≥10 | 0 | 0,0 % |

**Con rebalanceo cada 5 días hábiles: la regla se cumple en 40 de 157 fechas
(25,5 %).** La cartera quedaría neutra el 74,5 % del período → ~40 observaciones
útiles en 3 años. Horizonte 10 d: 24 de 79 (30,4 %).

**La pata BR es inejecutable:** nunca alcanza 4 señales simultáneas. Máximo 3, una
sola vez en 783 días. Participación en fechas de rebalanceo: ITUB **0/157**,
ABEV 1/157, BBD 11/157, PBR 26/157, VALE 31/157.

### 10.3 Diagnóstico — tres causas distintas, no una

| Ticker | Causa | ¿Reparable? |
|---|---|---|
| **ITUB** | **Bug de recall del builder, NO falta de cobertura.** Bloomberg usa el slug `itau`; la keyword derivada de la columna `nombre` ("Itau Unibanco") es `itau-unibanco` y no matchea. 7 slugs de Itaú aparecieron de rebote en las listas de AAPL, BBD y VALE. | **Sí** — `--keywords itau` |
| **VALE** | **52 % ruido.** El filtro CDX hace substring sobre la URL completa: entran `kering-to-buy-30-of-valentino`, `die-deutsche-bank-und-der-rivale-im-suden`, `amlo-...-prevalecera-su-legado`, `patissere-valerie`, `...cash-equivalents...`. Sólo 83 slugs empiezan por `vale-` y 40 más lo llevan como palabra. | Sí — filtrado posterior; real ≈ 123 |
| **BBD** | Cobertura real baja + **88,7 % en portugués**. Además contaminación cruzada (`vale-hires-lofiego-from-bradesco-bbi` es noticia de Vale). | No |
| **ABEV** | **Cobertura real casi nula: 3 artículos en 3 años, 100 % en portugués.** | No |
| **PBR** | Ninguna. Limpio, todos `petrobras-*`. 34,6 % en portugués. | — |

### 10.4 Decisiones tomadas el 2026-08-09, antes del backtest

1. **`FREEZE_DATE` movido 2026-08-10 → 2026-08-12.** Motivo: **datos, no resultados.**
   El backtest no se ha corrido; no existe ningún resultado que pudiera haber
   motivado el cambio. Congelar sobre un universo que la medición demuestra roto
   sería peor que mover la fecha 2 días. Deadline de competencia (16/08) sin riesgo.
2. **Idioma de señal: EN + PT.** Restringir a inglés dejaría BBD con ~6 artículos y
   ABEV con 0. Contrapartida asumida y registrada: hay que validar en Fase 2 que el
   extractor LLM mide el gap titular/cuerpo de forma comparable en ambos idiomas.
   Constante `SIGNAL_LANGUAGES = ("en", "pt")`.
3. **Re-medición en curso** con keywords corregidas: ITUB `--keywords itau`,
   ABEV `--keywords brahma,skol`, y segundo pase de BBD y PBR para recuperar los
   meses omitidos por 503/504.
4. **La regla de participación mínima NO se relajó.** Bajar `min_assets` de 6 a 4
   al descubrir que no se cumple es exactamente la decisión oportunista que el
   pre-registro busca impedir. Se mantiene en 6; si el universo no la soporta, se
   cambia el universo, no la regla.

### 10.6 CORRECCIÓN a §10.2 — el universo sí es viable

**Las conclusiones de §10.2 son incorrectas y quedan anuladas.** Se conservan
arriba por trazabilidad del registro, no como hallazgo válido.

El cálculo de §10.2 contaba una señal como válida **sólo el día exacto de
publicación del artículo**. Esa nunca fue la especificación de la estrategia: la
ventana de agregación al rebalanceo no está definida en ningún punto del repo, y
un gap de sentimiento no caduca en 24 h. Con agregación sobre los últimos K días
hábiles, y universo de 12 (VALE filtrado, ITUB recuperado):

| K (días hábiles) | ≥6 señales en rebalanceos 5d |
|---|---|
| 1 (lo medido en §10.2) | 39/157 — 24,8 % |
| 2 | 112/157 — 71,3 % |
| 3 | 148/157 — 94,3 % |
| **5** | **155/157 — 98,7 %** |
| 10 | 157/157 — 100 % |

**Con K = 5 la regla de participación mínima se cumple en el 98,7 % de los
rebalanceos.** La pata BR participa: PBR 52,9 %, VALE 43,9 %, ITUB 36,9 %,
BBD 22,9 %.

Quedan también anuladas la conclusión de §10.2 de que "la pata BR es inejecutable"
y la de §10.5 de que el reparto 7/5 no es sostenible: sí lo es, con la salvedad
de ABEV (§10.8).

### 10.7 K debe pre-registrarse AHORA

K es exactamente el tipo de parámetro que, elegido después de ver resultados, se
convierte en overfitting: la tabla de arriba muestra que mover K de 1 a 5 cambia
la participación del 25 % al 99 %. **Debe fijarse antes del backtest y por
razones estructurales, no por su efecto en el Sharpe.**

Razón estructural para **K = 5**: coincide con el horizonte corto declarado de la
estrategia (5 días hábiles). No se elige por maximizar participación — K = 10 da
100 % y aun así se descarta, porque una ventana de 10 días solaparía dos períodos
de rebalanceo consecutivos y reciclaría la misma noticia en dos decisiones.

### 10.8 Lo único que no sobrevive: ABEV

ABEV mantiene **4 artículos en 3 años** y **1,9 % de participación incluso con
K = 5** (3 de 157 rebalanceos). No es recall: el re-scan con `--keywords
brahma,skol` añadió 1 solo artículo. Es ausencia real de cobertura Bloomberg.

El reemplazo natural es **NU**, ya identificado en §6 antes de tener datos
(ADV 9× el de ABEV). Pendiente: recolectar NU y confirmar con números antes del
`FREEZE_DATE`.

### 10.9 Resolución — decisiones aplicadas el 2026-08-09

**Swap ABEV → NU: APLICADO**, con la evidencia medida abajo.

| | ABEV | NU |
|---|---|---|
| URLs procesables | 4 | **104** |
| Días con artículo | 3 | **57** |
| % días hábiles | 0,4 % | 7,3 % |
| Participación K=5 | 3/157 — **1,9 %** | 42/157 — **26,8 %** |

| Composición | Regla ≥6 | Pata BR con ≥3 señales |
|---|---|---|
| con ABEV | 155/157 (98,7 %) | 32/157 (20,4 %) |
| **con NU** | 155/157 (98,7 %) | **45/157 (28,7 %)** |

El swap **no mueve la regla global** — los 7 US ya la saturan. Paga en la pata BR:
+41 % de fechas con 3+ señales brasileñas, que es lo que sostiene el
market-neutral geográfico en la práctica y no sólo como etiqueta.

Bracket de costos de NU: `C_adr_medio` (15 bps), por ADV 528,8 M USD/día.
Se mantiene el reparto 7 US + 5 BR del criterio (c).

**`SIGNAL_AGGREGATION_WINDOW_DAYS = 5`: PRE-REGISTRADO** en `universe_final.py`
con la justificación estructural de §10.7.

### Universo final confirmado (12)

| | Tickers |
|---|---|
| **US (7)** | AAPL, MSFT, NVDA, AMZN, TSLA, META, JPM |
| **BR (5)** | PBR, VALE, ITUB, BBD, **NU** |

Verificado: `n=12`, `US=7`, `BR=5`, `ABEV` fuera, asserts de invariantes en verde,
suite existente `53 passed`.

### 10.5 Pendiente al cerrar esta entrada

- Conteos definitivos de ITUB y ABEV tras el re-scan.
- Filtrado del ruido de VALE (nota: el merge del builder respeta filas existentes
  pero re-añade las nuevas, así que la limpieza debe ser el último paso).
- Composición final de la pata BR: los datos actuales sólo soportan PBR, VALE y
  —si el re-scan lo confirma— ITUB. El reparto 7 US / 5 BR de §1(c) puede no ser
  sostenible.
