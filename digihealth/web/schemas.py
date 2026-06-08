"""Validazione e normalizzazione dei payload di alert in arrivo.

Il mittente puo' inviare due varianti di `air_quality_alert`:

  - SEMPLICE (campi flat): trigger_metrica, trigger_valore, level,
    recommended_action, action_code, urgency, ...
  - COMPLETA (schema_version "1.0"): oggetti annidati source/trigger/
    current_status/current_values/predictions/recommendation/delivery.

`parse_alert` accetta entrambe in modo tollerante (campi extra ammessi) e
restituisce un `NormalizedAlert` con i campi comuni usati come colonne SQLite.
Il payload grezzo viene comunque conservato integralmente.
"""
from typing import Any, Dict, Optional
from pydantic import BaseModel, ConfigDict, ValidationError


class AlertEnvelope(BaseModel):
    """Validazione minima e tollerante: serve solo a garantire che sia un
    alert ben formato. Tutti i campi extra sono mantenuti."""
    model_config = ConfigDict(extra="allow")

    event_type: str


class NormalizedAlert(BaseModel):
    """Rappresentazione piatta usata per indicizzare l'alert in SQLite."""
    event_type: str
    schema_version: Optional[str] = None
    timestamp: Optional[str] = None
    client_id: Optional[str] = None
    lampada: Optional[str] = None
    stanza: Optional[str] = None
    host: Optional[str] = None
    trigger_metric: Optional[str] = None
    trigger_value: Optional[float] = None
    level: Optional[str] = None
    overall_status: Optional[str] = None
    dominant_pollutant: Optional[str] = None
    action_code: Optional[str] = None
    recommended_action: Optional[str] = None
    urgency: Optional[str] = None
    action_due_minutes: Optional[float] = None
    raw: Dict[str, Any]


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def parse_alert(payload: Dict[str, Any]) -> NormalizedAlert:
    """Valida e normalizza il payload. Solleva ValidationError se non valido."""
    AlertEnvelope(**payload)  # validazione minima (presenza di event_type)

    source = payload.get("source") or {}
    trigger = payload.get("trigger") or {}
    recommendation = payload.get("recommendation") or {}
    current_status = payload.get("current_status") or {}

    return NormalizedAlert(
        event_type=payload["event_type"],
        schema_version=payload.get("schema_version"),
        timestamp=payload.get("timestamp"),
        # source.* (variante completa) oppure campi flat (variante semplice)
        client_id=source.get("client_id", payload.get("client_id")),
        lampada=source.get("lampada", payload.get("lampada")),
        stanza=source.get("stanza", payload.get("stanza")),
        host=source.get("host", payload.get("host")),
        # trigger.* oppure trigger_metrica/trigger_valore flat
        trigger_metric=trigger.get("raw_metric")
        or trigger.get("metric")
        or payload.get("trigger_metrica"),
        trigger_value=_to_float(trigger.get("value", payload.get("trigger_valore"))),
        level=trigger.get("level", payload.get("level")),
        overall_status=current_status.get("overall_status", payload.get("overall_status")),
        dominant_pollutant=current_status.get(
            "dominant_pollutant", payload.get("dominant_pollutant")
        ),
        # recommendation.* oppure campi flat
        action_code=recommendation.get("action_code", payload.get("action_code")),
        recommended_action=recommendation.get(
            "recommended_action", payload.get("recommended_action")
        ),
        urgency=recommendation.get("urgency", payload.get("urgency")),
        action_due_minutes=_to_float(
            recommendation.get("action_due_minutes", payload.get("action_due_minutes"))
        ),
        raw=payload,
    )


__all__ = ["AlertEnvelope", "NormalizedAlert", "parse_alert", "ValidationError"]
