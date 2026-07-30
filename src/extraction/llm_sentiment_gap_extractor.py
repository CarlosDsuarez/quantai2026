"""
Sentiment Gap Extractor — Desafio Quant AI 2026
Extrae sentiment de titular y cuerpo de forma AISLADA (sin contaminación
del titular sobre el cuerpo) para construir la señal: gap = body - headline.

Requiere: pip install anthropic --break-system-packages
Requiere: variable de entorno ANTHROPIC_API_KEY
"""

import anthropic
import json
import time
from dataclasses import dataclass, asdict
from typing import Optional

_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    """Singleton con init diferido: ANTHROPIC_API_KEY solo se exige al llamar
    a la API, no al importar el modulo."""
    global _client
    if _client is None:
        _client = anthropic.Anthropic()  # lee ANTHROPIC_API_KEY del entorno
    return _client

MODEL_BATCH = "claude-haiku-4-5-20251001"   # para correr a volumen (costo bajo)
MODEL_VALIDATION = "claude-sonnet-5"        # para validar muestra manual

# ---------------------------------------------------------------------------
# PASO 1: Sentiment del CUERPO (sin ver el titular)
# ---------------------------------------------------------------------------
SYSTEM_BODY = """Eres un analista de sentimiento financiero. Tu única tarea es
evaluar el sentimiento del CUERPO de un artículo de noticias financieras
respecto a una empresa específica, ignorando cualquier titular.

Responde ÚNICAMENTE con un objeto JSON válido, sin texto adicional, con este
esquema exacto:
{
  "is_primary_subject": true/false,   // la empresa es tema central del articulo (no solo mencionada de paso)
  "body_sentiment": float,             // -1.0 (muy negativo) a +1.0 (muy positivo), 0.0 = neutral
  "confidence": float,                 // 0.0 a 1.0, que tan seguro estas del score
  "rationale": string                  // una frase (max 20 palabras) justificando el score
}

Criterios de evaluacion: impacto esperado en fundamentales, guidance,
resultados operativos, riesgos regulatorios/legales, cambios estrategicos.
No evalues calidad de la escritura, solo el contenido de negocio."""

def build_body_prompt(ticker: str, company_name: str, body_text: str) -> str:
    return f"""Empresa objetivo: {company_name} (ticker: {ticker})

CUERPO DEL ARTICULO:
{body_text}

Evalua el sentimiento del cuerpo respecto a esta empresa segun el esquema JSON indicado."""


# ---------------------------------------------------------------------------
# PASO 2: Sentiment del TITULAR (llamada separada, sin ver el cuerpo)
# ---------------------------------------------------------------------------
SYSTEM_HEADLINE = """Eres un analista de sentimiento financiero. Tu unica
tarea es evaluar el sentimiento de un TITULAR de noticia financiera respecto
a una empresa especifica.

Responde UNICAMENTE con un objeto JSON valido, sin texto adicional:
{
  "headline_sentiment": float,   // -1.0 a +1.0
  "confidence": float,           // 0.0 a 1.0
  "rationale": string             // una frase (max 15 palabras)
}"""

def build_headline_prompt(ticker: str, company_name: str, headline: str) -> str:
    return f"""Empresa objetivo: {company_name} (ticker: {ticker})

TITULAR:
{headline}

Evalua el sentimiento del titular respecto a esta empresa segun el esquema JSON indicado."""


# ---------------------------------------------------------------------------
# Orquestacion
# ---------------------------------------------------------------------------
@dataclass
class SentimentGapResult:
    ticker: str
    headline: str
    headline_sentiment: Optional[float]
    body_sentiment: Optional[float]
    gap: Optional[float]
    is_primary_subject: Optional[bool]
    confidence_headline: Optional[float]
    confidence_body: Optional[float]
    rationale_headline: Optional[str]
    rationale_body: Optional[str]
    error: Optional[str] = None


def _call_and_parse(system: str, user: str, model: str, max_retries: int = 3) -> dict:
    for attempt in range(max_retries):
        try:
            resp = _get_client().messages.create(
                model=model,
                max_tokens=300,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            text = resp.content[0].text.strip()
            # limpia posibles fences de markdown si el modelo los agrega
            text = text.replace("```json", "").replace("```", "").strip()
            return json.loads(text)
        except (json.JSONDecodeError, IndexError) as e:
            if attempt == max_retries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("No se pudo parsear respuesta tras reintentos")


def extract_sentiment_gap(
    ticker: str,
    company_name: str,
    headline: str,
    body_text: str,
    model: str = MODEL_BATCH,
) -> SentimentGapResult:
    try:
        body_result = _call_and_parse(
            SYSTEM_BODY, build_body_prompt(ticker, company_name, body_text), model
        )
        headline_result = _call_and_parse(
            SYSTEM_HEADLINE, build_headline_prompt(ticker, company_name, headline), model
        )

        gap = body_result["body_sentiment"] - headline_result["headline_sentiment"]

        return SentimentGapResult(
            ticker=ticker,
            headline=headline,
            headline_sentiment=headline_result["headline_sentiment"],
            body_sentiment=body_result["body_sentiment"],
            gap=round(gap, 3),
            is_primary_subject=body_result["is_primary_subject"],
            confidence_headline=headline_result["confidence"],
            confidence_body=body_result["confidence"],
            rationale_headline=headline_result["rationale"],
            rationale_body=body_result["rationale"],
        )
    except Exception as e:
        return SentimentGapResult(
            ticker=ticker, headline=headline, headline_sentiment=None,
            body_sentiment=None, gap=None, is_primary_subject=None,
            confidence_headline=None, confidence_body=None,
            rationale_headline=None, rationale_body=None, error=str(e),
        )


if __name__ == "__main__":
    # ---- TEST INDIVIDUAL: reemplaza con el articulo real de TSLA 2007 ----
    headline = "PEGA AQUI EL TITULAR REAL DEL ARTICULO"
    body = "PEGA AQUI EL CUERPO COMPLETO DEL ARTICULO"

    result = extract_sentiment_gap(
        ticker="TSLA", company_name="Tesla Motors", headline=headline, body_text=body
    )
    print(json.dumps(asdict(result), indent=2, ensure_ascii=False))
