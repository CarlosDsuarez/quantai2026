"""
Portfolio Construction — ENTRELINHAS / Desafio Quant AI 2026 (Fase 3: backtest)

Por ahora este modulo contiene UNICAMENTE la regla de participacion minima,
pre-registrada el 2026-08-08 ANTES de correr cualquier backtest. Deliberadamente
no esta cableada a ningun pipeline todavia: el resto de la Fase 3 aun no existe,
y cablearla a medias invitaria a ajustarla despues de ver resultados — que es
exactamente lo que el pre-registro busca impedir.

Ver docs/decision_universo.md, seccion "Regra de participacao minima".
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

# Umbral congelado. Duplicado como default del parametro para que la funcion sea
# usable de forma aislada; la fuente de verdad es config/universe_final.py
# (MIN_ASSETS_PER_REBALANCE). No se importa, para mantener la funcion pura y sin
# dependencias de configuracion.
DEFAULT_MIN_ASSETS = 6

_INFINITIES = (float("inf"), float("-inf"))


def _is_valid_signal(value: Any) -> bool:
    """Una senal es valida si es un numero real finito.

    Invalidos: None, NaN, +/-inf, y cualquier cosa no numerica.
    Los booleanos se rechazan explicitamente: en Python `bool` es subclase de
    `int`, y un True colandose como senal seria un bug silencioso.
    """
    if value is None or isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return value == value and value not in _INFINITIES  # NaN != NaN


def has_minimum_participation(
    signals_at_date: Mapping[str, Any] | Iterable[Any],
    min_assets: int = DEFAULT_MIN_ASSETS,
) -> bool:
    """True si hay senal valida en al menos `min_assets` activos distintos.

    Regra de participacao minima (pre-registrada 2026-08-08, antes do backtest):
    em cada data de rebalanceamento exige-se sinal valido em pelo menos 6 ativos.
    Se houver menos de 6 sinais validos, a carteira permanece neutra (sem
    posicao) naquele periodo.

    Racional: com poucos ativos, uma carteira long-short deixa de ser
    market-neutral e passa a ser uma aposta idiossincratica.

    Funcion pura: no lee archivos, no muta la entrada, no depende de estado
    global. El llamador decide que hacer con el False (quedarse plano).

    Args:
        signals_at_date: mapping ticker -> senal (p.ej. {"AAPL": 0.42,
            "PBR": None}), o una secuencia de senales. Con un mapping se cuentan
            TICKERS distintos con senal valida; con una secuencia, elementos
            validos. Un `pandas.Series` entra por la rama de mapping porque
            expone `.items()`.
        min_assets: umbral inclusivo. >= min_assets devuelve True.

    Returns:
        True si el numero de senales validas es >= min_assets.

    Raises:
        ValueError: si min_assets < 1.
    """
    if min_assets < 1:
        raise ValueError(f"min_assets debe ser >= 1, recibido {min_assets}")

    # dict y pandas.Series exponen .items(); una lista/tupla no.
    items = getattr(signals_at_date, "items", None)
    if callable(items):
        valid = {key for key, value in items() if _is_valid_signal(value)}
        return len(valid) >= min_assets

    return sum(1 for value in signals_at_date if _is_valid_signal(value)) >= min_assets
