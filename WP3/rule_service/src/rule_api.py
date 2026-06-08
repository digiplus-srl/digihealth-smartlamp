"""Servizio FastAPI dedicato al motore rule-based (Fase 2).

Endpoint:
- GET  /api/rule/health
- POST /api/rule/evaluate

Avvio:
  uvicorn src.rule_api:app --host 0.0.0.0 --port 8010
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import pandas as pd
from fastapi import FastAPI, HTTPException
from influxdb_client import InfluxDBClient
from pydantic import BaseModel, Field

from src.phase2_rule_engine import (
    add_dynamics,
    add_trajectory_phase,
    infer_source,
    recommend_actions,
    run_rule_engine,
)

logger = logging.getLogger("airquality_rule_service")
if not logger.handlers:
        logging.basicConfig(level=logging.INFO)

ALERT_BATCH_WINDOW_MS = int(os.getenv("ALERT_BATCH_WINDOW_MS", "5000"))
INFLUX_URL = os.getenv("INFLUX_URL", "")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "")
INFLUX_ORG = os.getenv("INFLUX_ORG", "")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "health_data")
INFLUX_MEASUREMENT = os.getenv("INFLUX_MEASUREMENT", "ZPHSensor_sensore")
DOWNSTREAM_URL = os.getenv("ALERT_DOWNSTREAM_URL", "")
THIRD_PARTY_BASE_URL = os.getenv("THIRD_PARTY_BASE_URL", "")
THIRD_PARTY_USER_TOKEN = os.getenv("THIRD_PARTY_USER_TOKEN", "")
THIRD_PARTY_COMPANY_ID = int(os.getenv("THIRD_PARTY_COMPANY_ID", "0"))
THIRD_PARTY_ALARM_CATEGORY_ID = int(os.getenv("THIRD_PARTY_ALARM_CATEGORY_ID", "0"))
THIRD_PARTY_TOLERANCE_CATEGORY_ID = int(os.getenv("THIRD_PARTY_TOLERANCE_CATEGORY_ID", "0"))
THIRD_PARTY_PREDICTIVE_CATEGORY_ID = int(os.getenv("THIRD_PARTY_PREDICTIVE_CATEGORY_ID", "0"))
THIRD_PARTY_ASSET_ID_MAP_RAW = os.getenv("THIRD_PARTY_ASSET_ID_MAP", "{}")
THIRD_PARTY_ALERT_TIMEZONE = os.getenv("THIRD_PARTY_ALERT_TIMEZONE", "Europe/Rome")
PREDICTIVE_TTT_MAX_MINUTES = float(os.getenv("PREDICTIVE_TTT_MAX_MINUTES", "15"))
PREDICTIVE_ALERT_COOLDOWN_MINUTES = float(os.getenv("PREDICTIVE_ALERT_COOLDOWN_MINUTES", "60"))
DIGIHEALTH_ALERT_URL = os.getenv("DIGIHEALTH_ALERT_URL", "")
DIGIHEALTH_API_KEY = os.getenv("DIGIHEALTH_API_KEY", "")
DIGIHEALTH_LAMP1_ALERT_URL = os.getenv("DIGIHEALTH_LAMP1_ALERT_URL", "")
DIGIHEALTH_LAMP1_API_KEY = os.getenv("DIGIHEALTH_LAMP1_API_KEY", "")
DIGIHEALTH_LAMP1_LAMPS_RAW = os.getenv("DIGIHEALTH_LAMP1_LAMPS", "")
DIGIHEALTH_TIMEOUT_SECONDS = float(os.getenv("DIGIHEALTH_TIMEOUT_SECONDS", "15"))

INFLUX_EVALUATE_ALLOWED_FIELDS = [
    "CO2-AnidrideCarbonica-[ppm]",
    "TVOC-QualitaAria-[G]",
    "VOC-CompostiOrganiciVolatili",
    "CH2O-Formaldeide-[mg/m^3]",
    "CH2O-Formaldeie-[mg/m^3]",
    "CH2O-Formaldeie-[µg/m^3]",
    "PM1-Particolato-[µg/m^3]",
    "PM2_5-Particolato-[µg/m^3]",
    "PM10-Particolato-[µg/m^3]",
    "CO-MonossidoDiCarbonio-[ppm]",
    "O3-Ozono-[ppm]",
    "NO2-BiossidoDiAzoto-[ppm]",
    "TEMP-[C]",
    "HUM-[%]",
    "lux-IntensitaLuminosa",
]

# Mapping esplicito: usa solo queste misure Influx per la valutazione.
INFLUX_EVALUATE_FIELD_MAP: Dict[str, List[str]] = {
    "CO2": ["CO2-AnidrideCarbonica-[ppm]"],
    "VoC": ["TVOC-QualitaAria-[G]", "VOC-CompostiOrganiciVolatili"],
    "PMS2_5": ["PM2_5-Particolato-[µg/m^3]", "IAQI_PM25"],
    "PMS10": ["PM10-Particolato-[µg/m^3]"],
    "T": ["TEMP-[C]"],
    "H": ["HUM-[%]"],
}

INFLUX_TRIGGER_TO_CANONICAL: Dict[str, str] = {
    "CO2-AnidrideCarbonica-[ppm]": "CO2",
    "TVOC-QualitaAria-[G]": "TVOC",
    "VOC-CompostiOrganiciVolatili": "TVOC",
    "CH2O-Formaldeide-[mg/m^3]": "CH2O",
    "CH2O-Formaldeie-[mg/m^3]": "CH2O",
    "CH2O-Formaldeie-[µg/m^3]": "CH2O",
    "PM1-Particolato-[µg/m^3]": "PM1",
    "PM2_5-Particolato-[µg/m^3]": "PMS2_5",
    "PM10-Particolato-[µg/m^3]": "PMS10",
    "CO-MonossidoDiCarbonio-[ppm]": "CO",
    "O3-Ozono-[ppm]": "O3",
    "NO2-BiossidoDiAzoto-[ppm]": "NO2",
    "TEMP-[C]": "TEMP",
    "HUM-[%]": "HUM",
    "lux-IntensitaLuminosa": "lux",
}

INFLUX_AI_ACTION_METRICS = {
    "CO2-AnidrideCarbonica-[ppm]",
    "PM2_5-Particolato-[µg/m^3]",
    "PM2_5-Particolato-[Âµg/m^3]",
    "PM10-Particolato-[µg/m^3]",
    "PM10-Particolato-[Âµg/m^3]",
}
INFLUX_LUX_METRICS = {"lux-IntensitaLuminosa"}
INFLUX_FALLBACK_ACTION = (
    "WP3_12",
    "Consigliato intervento: aumentare il ricambio d'aria e monitorare l'andamento nei prossimi minuti.",
)
INFLUX_LUX_ACTION = (
    "WP3_13",
    "Consigliato intervento: verificare il livello di illuminazione e adeguare la luce nell'ambiente.",
)

# Mapping WP3 action code -> CRM action type ID
# 3=Comunicazione, 4=Purificatore, 5=Condizionatore, 6=Finestra-Balcone, 7=Porta stanza
WP3_ACTION_TYPE: Dict[str, int] = {
    "WP3_01": 3,   # Comunicazione
    "WP3_02": 4,   # Purificatore
    "WP3_03": 4,   # Purificatore
    "WP3_04": 4,   # Purificatore
    "WP3_05": 4,   # Purificatore
    "WP3_06": 6,   # Finestra-Balcone
    "WP3_07": 6,   # Finestra-Balcone
    "WP3_08": 6,   # Finestra-Balcone
    "WP3_09": 4,   # Purificatore
    "WP3_10": 6,   # Finestra-Balcone
    "WP3_11": 4,   # Purificatore
    "WP3_12": 4,   # Purificatore
    "WP3_13": 3,   # Comunicazione
    "WP3_PRED_01": 4,  # Purificatore
    "WP3_PRED_02": 6,  # Finestra-Balcone
}

GroupKey = Tuple[str, str, str, str]
_event_buffer: Dict[GroupKey, List["RuleTestEvent"]] = {}
_buffer_lock = asyncio.Lock()
_flush_worker_task: Optional[asyncio.Task] = None
_shutdown_event = asyncio.Event()

_third_party_client: Optional[httpx.AsyncClient] = None
_third_party_token: Optional[str] = None
_third_party_token_exp: float = 0.0
_third_party_auth_lock = asyncio.Lock()
_predictive_alert_cache: Dict[Tuple[str, str, str, str, str, str], float] = {}

_wp3_dynamic_map: Optional[Dict[str, int]] = None
_wp3_dynamic_map_last_fetch: float = 0.0
_WP3_MAP_REFRESH_SECONDS = 3600.0

try:
    THIRD_PARTY_ASSET_ID_MAP: Dict[str, Any] = json.loads(THIRD_PARTY_ASSET_ID_MAP_RAW)
    if not isinstance(THIRD_PARTY_ASSET_ID_MAP, dict):
        THIRD_PARTY_ASSET_ID_MAP = {}
except json.JSONDecodeError:
    logger.warning("THIRD_PARTY_ASSET_ID_MAP non valido, uso mappa vuota")
    THIRD_PARTY_ASSET_ID_MAP = {}

DIGIHEALTH_LAMP1_LAMPS = {
    lampada.strip()
    for lampada in DIGIHEALTH_LAMP1_LAMPS_RAW.split(",")
    if lampada.strip()
}

# Modelli Pydantic per validazione input/output API
class RuleSensorReading(BaseModel):
    """Singola lettura sensoriale (tipicamente 1/min)."""

    ts: Optional[str] = Field(None, description="Timestamp ISO-8601 opzionale")
    CO2: float = Field(..., ge=0, le=10000, description="CO2 in ppm")
    VoC: float = Field(..., ge=0, le=5000, description="VOC in ppb")
    PMS2_5: float = Field(..., ge=0, le=1000, description="PM2.5 in ug/m3")
    PMS10: float = Field(..., ge=0, le=2000, description="PM10 in ug/m3")
    T: float = Field(..., ge=-20, le=60, description="Temperatura in C")
    H: float = Field(..., ge=0, le=100, description="Umidita relativa in %")


class RuleEvaluateRequest(BaseModel):
    """Richiesta di valutazione rule-based."""

    readings: List[RuleSensorReading] = Field(
        ...,
        min_length=1,
        max_length=5000,
        description="Serie letture ordinate nel tempo (unico campo necessario)",
    )


class RuleEvaluateLatestResponse(BaseModel):
    """Risposta sintetica: solo stato e consiglio correnti (ultima riga)."""

    overall_status: Optional[str] = None
    overall_severity: int = 0
    dominant_pollutant: Optional[str] = None
    trajectory_phase: Optional[str] = None
    trajectory_roc: float = 0.0
    trajectory_acc: float = 0.0
    ttt_minutes: Optional[float] = None
    recommended_action: Optional[str] = None
    action_code: Optional[str] = None
    urgency: Optional[str] = None
    action_due_minutes: Optional[int] = None
    action_confidence: float = 0.0


class RuleTestEvent(BaseModel):
    client_id: str
    lampada: str
    stanza: str
    host: str
    trigger_metrica: str
    trigger_valore: float
    threshold_warning: float
    threshold_critical: float
    level: str = Field(..., pattern="^(WARNING|CRITICAL)$")
    timestamp_alert: datetime


class InfluxAlertEvent(BaseModel):
    client_id: str
    lampada: str
    stanza: str
    host: str
    trigger_metrica: str
    trigger_valore: float
    threshold_warning: float
    threshold_critical: Optional[float] = None
    level: str = Field(..., pattern="^(OK|WARNING|CRITICAL)$")
    timestamp_alert: datetime
    predictive_only: bool = False
    prediction_target_level: Optional[str] = Field(None, pattern="^(WARNING|CRITICAL)$")


def _group_key(evt: RuleTestEvent) -> GroupKey:
    return (evt.client_id, evt.lampada, evt.stanza, evt.host)


def _group_to_payload_base(group: GroupKey) -> Dict[str, str]:
    return {
        "client_id": group[0],
        "lampada": group[1],
        "stanza": group[2],
        "host": group[3],
    }


def _dedupe_metrics(events: List[RuleTestEvent]) -> Dict[str, RuleTestEvent]:
    ordered = sorted(events, key=lambda e: e.timestamp_alert)
    latest_by_metric: Dict[str, RuleTestEvent] = {}
    for evt in ordered:
        latest_by_metric[evt.trigger_metrica] = evt
    return latest_by_metric


def _query_history_blocking(group: GroupKey, metrics: List[str]) -> List[Dict[str, Any]]:
    if not (INFLUX_URL and INFLUX_TOKEN and INFLUX_ORG) or not metrics:
        return []

    metric_set = set(metrics)
    flux = f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -10m)
  |> filter(fn: (r) => r._measurement == "{INFLUX_MEASUREMENT}")
  |> filter(fn: (r) => r.client_id == "{group[0]}")
  |> filter(fn: (r) => r.lampada == "{group[1]}")
  |> filter(fn: (r) => r.stanza == "{group[2]}")
  |> filter(fn: (r) => r.host == "{group[3]}")
  |> aggregateWindow(every: 1m, fn: last, createEmpty: false)
  |> keep(columns: ["_time", "_field", "_value"])
'''

    with InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG) as client:
        query_api = client.query_api()
        tables = query_api.query(query=flux)

    rows: List[Dict[str, Any]] = []
    for table in tables:
        for record in table.records:
            field = str(record.values.get("_field", ""))
            if field not in metric_set:
                continue
            value = record.values.get("_value")
            if value is None:
                continue
            ts_raw = record.values.get("_time")
            if hasattr(ts_raw, "to_pydatetime"):
                ts = ts_raw.to_pydatetime().astimezone(timezone.utc).isoformat()
            else:
                ts = str(ts_raw)

            rows.append(
                {
                    "metrica": field,
                    "timestamp": ts,
                    "valore": float(value),
                }
            )

    rows.sort(key=lambda x: (x["metrica"], x["timestamp"]))
    return rows


async def _query_history(group: GroupKey, metrics: List[str]) -> List[Dict[str, Any]]:
    try:
        return await asyncio.to_thread(_query_history_blocking, group, metrics)
    except Exception as exc:
        logger.exception("Errore query InfluxDB per group=%s: %s", group, exc)
        return []


async def _dispatch_downstream(payload: Dict[str, Any]) -> None:
    # Mantiene compatibilita con logica attuale (print/log locale) se URL non configurato.
    if not DOWNSTREAM_URL:
        logger.info("[rule-test][downstream-local] %s", payload)
        return

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(DOWNSTREAM_URL, json=payload)
        resp.raise_for_status()


def _jwt_exp_epoch(token: str) -> float:
    """Estrae exp da JWT (epoch secondi). Ritorna 0 se non disponibile."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return 0.0
        payload_b64 = parts[1]
        payload_b64 += "=" * ((4 - len(payload_b64) % 4) % 4)
        payload_raw = base64.urlsafe_b64decode(payload_b64.encode("ascii"))
        payload = json.loads(payload_raw.decode("utf-8"))
        return float(payload.get("exp", 0.0))
    except Exception:
        return 0.0


async def _get_third_party_client() -> httpx.AsyncClient:
    global _third_party_client
    if _third_party_client is None:
        _third_party_client = httpx.AsyncClient(
            base_url=THIRD_PARTY_BASE_URL.rstrip("/"),
            timeout=20.0,
            follow_redirects=True,
        )
    return _third_party_client


async def _reset_third_party_client() -> None:
    """Chiude e azzera il client httpx per forzare una sessione pulita al prossimo login."""
    global _third_party_client
    if _third_party_client is not None:
        try:
            await _third_party_client.aclose()
        except Exception:
            pass
        _third_party_client = None


async def _get_third_party_bearer(force_refresh: bool = False) -> str:
    if not THIRD_PARTY_BASE_URL:
        raise RuntimeError("THIRD_PARTY_BASE_URL non configurato.")

    global _third_party_token, _third_party_token_exp
    now = time.time()

    async with _third_party_auth_lock:
        if (
            not force_refresh
            and _third_party_token
            and (_third_party_token_exp <= 0 or _third_party_token_exp - now > 60)
        ):
            return _third_party_token

        login_body = {
            "UserToken": THIRD_PARTY_USER_TOKEN,
            "CompanyId": THIRD_PARTY_COMPANY_ID,
        }

        last_exc: Optional[Exception] = None
        for attempt in range(2):
            try:
                client = await _get_third_party_client()
                login_resp = await client.post("/api/token", json=login_body)
            except Exception as exc:
                logger.error("[auth] /api/token connessione fallita (attempt %d/2): %s", attempt + 1, exc)
                last_exc = exc
                await _reset_third_party_client()
                continue

            if login_resp.is_error:
                logger.error(
                    "[auth] /api/token %s (attempt %d/2): %s",
                    login_resp.status_code,
                    attempt + 1,
                    login_resp.text[:500],
                )
                last_exc = httpx.HTTPStatusError(
                    str(login_resp.status_code), request=login_resp.request, response=login_resp
                )
                await _reset_third_party_client()
                continue

            token = login_resp.text.strip().strip('"')
            if not token:
                raise RuntimeError("Token vuoto da /api/token")

            _third_party_token = token
            _third_party_token_exp = _jwt_exp_epoch(token)
            logger.info("[auth] token ottenuto con successo, exp_epoch=%.0f", _third_party_token_exp)
            return token

        # Entrambi i tentativi falliti: usa il token precedente se ancora valido
        if _third_party_token and (_third_party_token_exp <= 0 or _third_party_token_exp > now):
            logger.warning(
                "[auth] refresh fallito dopo 2 tentativi, uso token precedente ancora valido (exp_epoch=%.0f)",
                _third_party_token_exp,
            )
            return _third_party_token

        raise last_exc or RuntimeError("_get_third_party_bearer: login fallito")


def _extract_related_id(value: Any) -> Optional[Any]:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip().strip('"')
        if not stripped:
            return None
        if stripped.startswith(("{", "[")):
            try:
                return _extract_related_id(json.loads(stripped))
            except json.JSONDecodeError:
                return None
        if stripped.isdigit():
            return int(stripped)
        return None
    if isinstance(value, list):
        for item in value:
            found = _extract_related_id(item)
            if found is not None:
                return found
        return None
    if not isinstance(value, dict):
        return None

    priority_keys = (
        "comunicazioneId",
        "idComunicazione",
        "crmComunicazioneId",
        "CRMComunicazioneId",
        "oid",
        "Oid",
        "id",
        "Id",
    )
    for key in priority_keys:
        if key in value and value[key] is not None and str(value[key]).strip():
            return value[key]

    for key in ("jSonData", "jsonData", "data", "result", "value"):
        nested = value.get(key)
        if isinstance(nested, str):
            try:
                nested = json.loads(nested)
            except json.JSONDecodeError:
                continue
        found = _extract_related_id(nested)
        if found is not None:
            return found

    return None


def _third_party_alert_timestamp(dt: datetime) -> str:
    try:
        tz = ZoneInfo(THIRD_PARTY_ALERT_TIMEZONE)
    except ZoneInfoNotFoundError:
        tz = timezone.utc

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")


def _third_party_asset_id(lampada: str) -> Any:
    return THIRD_PARTY_ASSET_ID_MAP.get(lampada, lampada)


def _third_party_trigger_metric(metric: str) -> str:
    return INFLUX_TRIGGER_TO_CANONICAL.get(metric, metric)


def _alert_level_for_crm(evt: InfluxAlertEvent, latest: Dict[str, Any]) -> str:
    if latest.get("is_predictive"):
        return str(latest.get("predicted_level") or "WARNING").upper()
    return str(evt.level).upper()


def _threshold_for_crm(evt: InfluxAlertEvent, latest: Dict[str, Any]) -> Optional[float]:
    level = _alert_level_for_crm(evt, latest)
    if level == "CRITICAL" and evt.threshold_critical is not None:
        return evt.threshold_critical
    return evt.threshold_warning


def _category_for_crm(evt: InfluxAlertEvent, latest: Dict[str, Any]) -> int:
    if latest.get("is_predictive"):
        return THIRD_PARTY_PREDICTIVE_CATEGORY_ID
    return THIRD_PARTY_ALARM_CATEGORY_ID if _alert_level_for_crm(evt, latest) == "CRITICAL" else THIRD_PARTY_TOLERANCE_CATEGORY_ID


def _clean_sentence(text: Any) -> str:
    return " ".join(str(text or "").strip().split()).rstrip(". ")


def _join_sentences(*parts: Any) -> str:
    sentences = [_clean_sentence(part) for part in parts if _clean_sentence(part)]
    if not sentences:
        return ""
    return ". ".join(sentences) + "."


def _predictive_message_timing(ttt_minutes: Any) -> str:
    try:
        ttt = float(ttt_minutes)
    except (TypeError, ValueError):
        return "nei prossimi minuti"
    if ttt < 1:
        return "entro pochi minuti"
    return f"entro {round(ttt, 1)} minuti"


def _predictive_cache_key(evt: InfluxAlertEvent, predicted_level: str) -> Tuple[str, str, str, str, str, str]:
    return (
        str(evt.client_id),
        str(evt.lampada),
        str(evt.stanza),
        str(evt.host),
        str(evt.trigger_metrica),
        predicted_level,
    )


def _predictive_recently_sent(evt: InfluxAlertEvent, predicted_level: str) -> bool:
    key = _predictive_cache_key(evt, predicted_level)
    now = time.monotonic()
    last_sent = _predictive_alert_cache.get(key)
    if last_sent is None:
        return False
    return now - last_sent < PREDICTIVE_ALERT_COOLDOWN_MINUTES * 60


def _mark_predictive_sent(evt: InfluxAlertEvent, predicted_level: str) -> None:
    _predictive_alert_cache[_predictive_cache_key(evt, predicted_level)] = time.monotonic()


def _build_predictive_latest(evt: InfluxAlertEvent, latest: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if evt.trigger_metrica not in INFLUX_AI_ACTION_METRICS:
        return None

    current_level = str(evt.level).upper()
    requested_target = str(evt.prediction_target_level or "").upper()
    if requested_target in {"WARNING", "CRITICAL"}:
        predicted_level = requested_target
    else:
        if current_level == "CRITICAL":
            return None
        predicted_level = "CRITICAL" if current_level == "WARNING" else "WARNING"

    try:
        roc = float(latest.get("trigger_roc", latest.get("trajectory_roc", 0.0)) or 0.0)
    except (TypeError, ValueError):
        roc = 0.0
    if pd.isna(roc):
        roc = 0.0

    threshold = evt.threshold_critical if predicted_level == "CRITICAL" else evt.threshold_warning
    if threshold is None:
        return None

    try:
        current_value = float(evt.trigger_valore)
        threshold_value = float(threshold)
    except (TypeError, ValueError):
        return None

    if current_value >= threshold_value:
        return None

    ttt_raw = latest.get("ttt_minutes")
    ttt_minutes: Optional[float]
    try:
        ttt_minutes = None if ttt_raw is None or pd.isna(ttt_raw) else float(ttt_raw)
    except (TypeError, ValueError):
        ttt_minutes = None

    if requested_target or ttt_minutes is None:
        if roc <= 0:
            return None
        ttt_minutes = (threshold_value - current_value) / roc

    if pd.isna(ttt_minutes):
        return None

    if ttt_minutes < 0 or ttt_minutes > PREDICTIVE_TTT_MAX_MINUTES:
        return None

    phase = str(latest.get("trajectory_phase") or "")
    if roc <= 0 and phase != "accelerazione":
        return None

    if _predictive_recently_sent(evt, predicted_level):
        logger.info(
            "[rule-evaluate] predizione gia inviata di recente: trigger=%s predicted_level=%s",
            evt.trigger_metrica,
            predicted_level,
        )
        return None

    metric = _third_party_trigger_metric(evt.trigger_metrica)
    action_code = "WP3_PRED_02" if predicted_level == "CRITICAL" else "WP3_PRED_01"
    predicted_label = "critica" if predicted_level == "CRITICAL" else "warning"
    timing = _predictive_message_timing(ttt_minutes)
    action = (
        f"Predizione superamento soglia {predicted_label}: {metric} in crescita. "
        f"Valore attuale: {evt.trigger_valore}. Soglia: {threshold_value:g}. "
        f"Superamento stimato {timing}. Consigliato aumentare il ricambio d'aria."
    )

    predictive_latest = dict(latest)
    predictive_latest.update(
        {
            "is_predictive": True,
            "predicted_level": predicted_level,
            "dominant_pollutant": metric,
            "recommended_action": action,
            "action_code": action_code,
            "urgency": "alta" if predicted_level == "CRITICAL" else "media",
            "action_confidence": max(float(latest.get("action_confidence", 0.0) or 0.0), 0.8),
        }
    )
    return predictive_latest


async def _post_third_party_json(path: str, body: Dict[str, Any]) -> httpx.Response:
    client = await _get_third_party_client()
    token = await _get_third_party_bearer(force_refresh=False)
    headers = {"Authorization": f"Bearer {token}"}
    resp = await client.post(path, json=body, headers=headers)

    # Su 401/403 il token è scaduto/non valido: rifai login e riprova.
    # Su 400 potrebbe essere lo stesso problema su alcuni CRM.
    if resp.status_code in (400, 401, 403):
        logger.warning("[auth] %s su %s, force_refresh token e retry", resp.status_code, path)
        token = await _get_third_party_bearer(force_refresh=True)
        client = await _get_third_party_client()
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.post(path, json=body, headers=headers)

    resp.raise_for_status()
    return resp


async def _fetch_decision_matrix_map() -> Dict[str, int]:
    """Scarica la matrice decisionale dal CRM e restituisce mapping WP3 -> action_type_id.
    In caso di errore o parsing fallito, ritorna il dizionario hardcoded WP3_ACTION_TYPE."""
    try:
        body: Dict[str, Any] = {
            "SearchCriteriaMatriceDecisione": {},
            "Skip": 0,
            "Take": 200,
        }
        resp = await _post_third_party_json(
            "/api/digie/matriceDecisione/searchMatriciDecisione", body
        )
        raw = resp.json()
        logger.info("[decision-matrix] response keys: %s", list(raw.keys()) if isinstance(raw, dict) else type(raw).__name__)
        if isinstance(raw, dict) and "jSonData" in raw:
            jd = raw["jSonData"]
            logger.info("[decision-matrix] jSonData type=%s snippet=%s", type(jd).__name__, str(jd)[:500])

        items: List[Any] = []
        if isinstance(raw, list):
            items = raw
        else:
            # Cerca la lista navigando anche un livello annidato (es. jSonData.items)
            search_nodes = [raw]
            json_data = raw.get("jSonData")
            if isinstance(json_data, dict):
                search_nodes.append(json_data)
            elif isinstance(json_data, str) and json_data.strip().startswith("{"):
                try:
                    search_nodes.append(json.loads(json_data))
                except json.JSONDecodeError:
                    pass
            for node in search_nodes:
                if not isinstance(node, dict):
                    continue
                for key in ("items", "value", "data", "result", "results", "matrici", "list"):
                    candidate = node.get(key)
                    if isinstance(candidate, list):
                        items = candidate
                        break
                    if isinstance(candidate, str) and candidate.strip().startswith("["):
                        try:
                            parsed = json.loads(candidate)
                            if isinstance(parsed, list):
                                items = parsed
                                break
                        except json.JSONDecodeError:
                            pass
                if items:
                    break

        if not items:
            logger.warning("[decision-matrix] nessuna lista trovata nella response, uso fallback hardcoded. Keys: %s",
                           list(raw.keys()) if isinstance(raw, dict) else type(raw).__name__)
            return dict(WP3_ACTION_TYPE)

        result: Dict[str, int] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            code: Optional[str] = None
            for f in ("codiceErrore", "codiceWP3", "codice", "code", "wp3Code", "wp3", "Codice", "CodiceWP3", "CodiceAlert"):
                if item.get(f):
                    code = str(item[f]).strip()
                    break
            action_id: Optional[int] = None
            for f in ("tipoAzione", "actionType", "actionTypeId", "idTipoAzione",
                      "TipoAzione", "ActionType", "tipoazione", "action"):
                if item.get(f) is not None:
                    try:
                        action_id = int(item[f])
                        break
                    except (ValueError, TypeError):
                        pass
            if code and action_id is not None:
                result[code] = action_id

        if not result:
            logger.warning("[decision-matrix] parsing vuoto — primo item: %s | uso fallback",
                           json.dumps(items[0]) if items else "[]")
            return dict(WP3_ACTION_TYPE)

        logger.info("[decision-matrix] mappa dinamica caricata: %d entries: %s", len(result), result)
        return result

    except Exception as exc:
        logger.error("[decision-matrix] errore fetch: %s — uso fallback hardcoded", exc)
        return dict(WP3_ACTION_TYPE)


async def _get_wp3_action_type(action_code: str) -> int:
    """Ritorna il CRM action type ID per un codice WP3.
    La mappa viene aggiornata dal CRM ogni ora; fallback al dict hardcoded."""
    global _wp3_dynamic_map, _wp3_dynamic_map_last_fetch
    now = time.time()
    if _wp3_dynamic_map is None or (now - _wp3_dynamic_map_last_fetch) > _WP3_MAP_REFRESH_SECONDS:
        _wp3_dynamic_map = await _fetch_decision_matrix_map()
        _wp3_dynamic_map_last_fetch = now
    return _wp3_dynamic_map.get(action_code, WP3_ACTION_TYPE.get(action_code, 3))


def _json_number(value: Any) -> Any:
    try:
        if value is None or pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def _digihealth_alert_payload(evt: InfluxAlertEvent, latest: Dict[str, Any]) -> Dict[str, Any]:
    current_value = latest.get("trigger_valore", evt.trigger_valore)
    return {
        "event_type": "air_quality_alert",
        "client_id": str(evt.client_id),
        "lampada": str(evt.lampada),
        "stanza": str(evt.stanza),
        "trigger_metrica": str(evt.trigger_metrica),
        "trigger_valore": _json_number(current_value),
        "level": _alert_level_for_crm(evt, latest),
        "action_code": str(latest.get("action_code") or "WP3_11"),
        "recommended_action": str(latest.get("recommended_action") or ""),
        "urgency": str(latest.get("urgency") or "media"),
    }


def _digihealth_endpoint_for_lampada(lampada: Any) -> Tuple[str, str, str]:
    lampada_key = str(lampada or "")
    if lampada_key in DIGIHEALTH_LAMP1_LAMPS:
        return DIGIHEALTH_LAMP1_ALERT_URL, DIGIHEALTH_LAMP1_API_KEY, "lamp1"
    return DIGIHEALTH_ALERT_URL, DIGIHEALTH_API_KEY, "digihealth"


async def _send_alert_to_digihealth(evt: InfluxAlertEvent, latest: Dict[str, Any]) -> bool:
    alert_url, api_key, endpoint_name = _digihealth_endpoint_for_lampada(evt.lampada)
    if not (alert_url and api_key):
        logger.warning(
            "[rule-evaluate] endpoint DigiHealth %s non configurato per lampada %s",
            endpoint_name,
            evt.lampada,
        )
        return False

    body = _digihealth_alert_payload(evt, latest)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "X-Api-Key": api_key,
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=DIGIHEALTH_TIMEOUT_SECONDS) as client:
        resp = await client.post(alert_url, json=body, headers=headers)
        resp.raise_for_status()

    logger.info("[rule-evaluate] alert DigiHealth inviato a %s: %s", endpoint_name, body)
    return True


async def _save_third_party_storico_alert(
    evt: InfluxAlertEvent,
    latest: Dict[str, Any],
    comunicazione_id: Optional[Any],
) -> None:
    if comunicazione_id is None:
        logger.warning("[rule-evaluate] storico alert senza idComunicazione: skip")
        return

    action = str(latest.get("recommended_action") or "")
    action_code = str(latest.get("action_code") or "WP3_11")
    lampada = str(evt.lampada or "")
    current_value = latest.get("trigger_valore", evt.trigger_valore)
    threshold = _threshold_for_crm(evt, latest)
    level = _alert_level_for_crm(evt, latest)
    threshold_label = "critica" if level == "CRITICAL" else "warning"
    action_message = _clean_sentence(action)
    value_detail = f"Valore rilevato: {current_value}"
    threshold_detail = f"Soglia {threshold_label}: {threshold}"

    if latest.get("is_predictive"):
        message = _join_sentences(action_message)
        if action_message and not action_message.lower().startswith("predizione"):
            message = _join_sentences("Predizione", action_message)
        if "valore " not in action_message.lower() and "soglia" not in action_message.lower():
            message = _join_sentences(message, value_detail, threshold_detail)
    else:
        message = _join_sentences(action_message, value_detail, threshold_detail)

    body = {
        "storicoAlertId": None,
        "codiceAlert": action_code,
        "action": await _get_wp3_action_type(action_code),
        "messaggioAlert": message,
        "triggerValore": current_value,
        "triggerMetrica": _third_party_trigger_metric(evt.trigger_metrica),
        "tipoSoglia": 1 if level == "CRITICAL" else 0,
        "timeStampDateAlert": _third_party_alert_timestamp(evt.timestamp_alert),
        "stanza": evt.stanza,
        "idRelated": comunicazione_id,
        "assemblyRelated": "CRMCore.BO.CRMComunicazione",
        "idGenericRelated": comunicazione_id,
        "assemblyFullNameGenericRelated": "comunicazione",
        "assetId": _third_party_asset_id(lampada),
    }

    logger.info("[rule-evaluate] payload storico alert CRM: %s", body)
    await _post_third_party_json("/api/digiE/storicoAlert/SaveStoricoAlert", body)


async def _send_alert_to_third_party(evt: InfluxAlertEvent, latest: Dict[str, Any]) -> None:
    """Invia comunicazione e storico alert al sistema terzo."""
    if not THIRD_PARTY_BASE_URL:
        return

    dominant = str(latest.get("dominant_pollutant") or "NESSUNO")
    action = str(latest.get("recommended_action") or "")
    action_code = str(latest.get("action_code") or "WP3_11")
    lampada = str(evt.lampada or "")
    current_value = latest.get("trigger_valore", evt.trigger_valore)
    threshold = _threshold_for_crm(evt, latest)
    is_predictive = bool(latest.get("is_predictive"))
    subject_prefix = "Predizione - " if is_predictive else ""
    description_title = "Predizione mitigazione" if is_predictive else "Consiglio mitigazione"

    oggetto = f"{subject_prefix}Lampada: {lampada} - {description_title} ({dominant})"
    intro = f"{evt.trigger_metrica}: {current_value} - Soglia: {threshold}"
    descrizione = (
        f"{description_title} ({dominant})\n\n"
        f"Codice Azione: {action_code}: [{action}]"
    )

    try:
        destinatario: Any = int(str(evt.client_id))
    except ValueError:
        destinatario = str(evt.client_id)

    body = {
        "comunicazioneId": None,
        "descrizione": descrizione,
        "oggetto": oggetto,
        "dataComunicazione": None,
        "intro": intro,
        "selectedCategoriaId": _category_for_crm(evt, latest),
        "selectedDestinatariId": [destinatario],
        "statoInvio": 0,
        "aTutti": False,
    }

    resp = await _post_third_party_json("/api/comunicazione/comunicazione/saveComunicazione", body)
    try:
        comunicazione_payload = resp.json()
    except json.JSONDecodeError:
        comunicazione_payload = resp.text

    comunicazione_id = _extract_related_id(comunicazione_payload)
    logger.info("[rule-evaluate] comunicazione CRM salvata, id=%s", comunicazione_id)
    await _save_third_party_storico_alert(evt, latest, comunicazione_id)
    if is_predictive:
        _mark_predictive_sent(evt, _alert_level_for_crm(evt, latest))


async def _send_alert_notifications(evt: InfluxAlertEvent, latest: Dict[str, Any]) -> None:
    sent = False

    if THIRD_PARTY_BASE_URL:
        try:
            await _send_alert_to_third_party(evt, latest)
            sent = True
        except Exception as exc:
            logger.exception("[rule-evaluate] invio CRM fallito: %s", exc)

    if (DIGIHEALTH_ALERT_URL and DIGIHEALTH_API_KEY) or (DIGIHEALTH_LAMP1_ALERT_URL and DIGIHEALTH_LAMP1_API_KEY):
        try:
            sent = await _send_alert_to_digihealth(evt, latest) or sent
        except Exception as exc:
            logger.exception("[rule-evaluate] invio DigiHealth fallito: %s", exc)

    if sent and latest.get("is_predictive"):
        _mark_predictive_sent(evt, _alert_level_for_crm(evt, latest))


async def _flush_group(group: GroupKey, events: List[RuleTestEvent]) -> None:
    started = time.perf_counter()

    latest_by_metric = _dedupe_metrics(events)
    metrics = sorted(latest_by_metric.keys())
    history = await _query_history(group, metrics)

    latest_ts = max((evt.timestamp_alert for evt in events), default=datetime.now(timezone.utc))

    payload = {
        **_group_to_payload_base(group),
        "metrics_alert": [
            {
                "metrica": evt.trigger_metrica,
                "valore": evt.trigger_valore,
                "warning": evt.threshold_warning,
                "critical": evt.threshold_critical,
                "level": evt.level,
            }
            for evt in latest_by_metric.values()
        ],
        "metrics_history": history,
        "timestamp_alert": latest_ts.astimezone(timezone.utc).isoformat(),
    }

    await _dispatch_downstream(payload)

    flush_ms = (time.perf_counter() - started) * 1000.0
    payload_size = len(json.dumps(payload, default=str))
    logger.info(
        "[rule-test][flush] group=%s events=%d metrics=%d payload_bytes=%d flush_ms=%.2f",
        "|".join(group),
        len(events),
        len(latest_by_metric),
        payload_size,
        flush_ms,
    )


async def _flush_buffer_once() -> None:
    async with _buffer_lock:
        if not _event_buffer:
            return
        snapshot = dict(_event_buffer)
        _event_buffer.clear()

    tasks = [_flush_group(group, events) for group, events in snapshot.items()]
    await asyncio.gather(*tasks, return_exceptions=False)


async def _flush_worker() -> None:
    wait_seconds = max(ALERT_BATCH_WINDOW_MS, 500) / 1000.0
    logger.info("[rule-test] flush worker started, window_ms=%d", ALERT_BATCH_WINDOW_MS)
    while not _shutdown_event.is_set():
        try:
            await asyncio.wait_for(_shutdown_event.wait(), timeout=wait_seconds)
        except asyncio.TimeoutError:
            pass
        await _flush_buffer_once()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _flush_worker_task, _third_party_client
    _shutdown_event.clear()
    _flush_worker_task = asyncio.create_task(_flush_worker())
    try:
        yield
    finally:
        _shutdown_event.set()
        await _flush_buffer_once()
        if _flush_worker_task:
            await _flush_worker_task
        if _third_party_client is not None:
            await _reset_third_party_client()
        _third_party_token = None
        _third_party_token_exp = 0.0


app = FastAPI(
    title="AirQuality Rule-Based API",
    version="1.0.0",
    description=(
        "Servizio FastAPI per eseguire il motore rule-based "
        "su serie di letture sensoriali."
    ),
    lifespan=lifespan,
)


@app.get("/api/rule/health")
async def health() -> Dict[str, str]:
    """Health check servizio rule-based."""
    return {"status": "ok", "service": "rule-based", "engine": "phase2_rule_engine"}


@app.post("/api/rule/test")
async def test_json(payload: RuleTestEvent) -> Dict[str, Any]:
    """Riceve alert da Flux task e li accoda per aggregazione batch."""
    key = _group_key(payload)

    async with _buffer_lock:
        bucket = _event_buffer.setdefault(key, [])
        bucket.append(payload)
        queued_for_group = len(bucket)

    return {
        "status": "buffered",
        "group_key": {
            "client_id": key[0],
            "lampada": key[1],
            "stanza": key[2],
            "host": key[3],
        },
        "queued_events": queued_for_group,
        "flush_window_ms": ALERT_BATCH_WINDOW_MS,
    }


def _to_dataframe(site: str, room: str, readings: List[RuleSensorReading]) -> pd.DataFrame:
    rows = [r.model_dump() for r in readings]
    df = pd.DataFrame(rows)

    if "ts" in df.columns:
        df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
        if df["ts"].notna().any():
            df = df.sort_values("ts")

    df["site"] = site
    df["room"] = room

    ordered_cols = ["ts", "site", "room", "CO2", "VoC", "PMS2_5", "PMS10", "T", "H"]
    cols = [c for c in ordered_cols if c in df.columns]
    return df[cols].reset_index(drop=True)


def _query_influx_readings_blocking(alert: InfluxAlertEvent, lookback_minutes: int = 10) -> List[RuleSensorReading]:
    if not (INFLUX_URL and INFLUX_TOKEN and INFLUX_ORG):
        raise RuntimeError("Configurazione Influx incompleta (INFLUX_URL/INFLUX_TOKEN/INFLUX_ORG).")

    field_names = sorted(
        set(INFLUX_EVALUATE_ALLOWED_FIELDS)
        | {name for aliases in INFLUX_EVALUATE_FIELD_MAP.values() for name in aliases}
    )
    field_filter = " or ".join(f'r._field == {json.dumps(name, ensure_ascii=False)}' for name in field_names)

    identity_filters = [
        f'  |> filter(fn: (r) => exists r.lampada and r.lampada == {json.dumps(str(alert.lampada), ensure_ascii=False)})',
        f'  |> filter(fn: (r) => exists r.stanza and r.stanza == {json.dumps(str(alert.stanza), ensure_ascii=False)})',
        f'  |> filter(fn: (r) => exists r.host and r.host == {json.dumps(str(alert.host), ensure_ascii=False)})',
    ]
    # Non filtriamo su client_id: non tutti i device lo hanno come tag in health_data

    # Filtra il device prima del pivot e usa solo _time come row key: alcuni stream
    # non hanno client_id come colonna/tag e Influx fallisce se la si mette nel rowKey.
    flux = f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -{lookback_minutes}m)
  |> filter(fn: (r) => r._measurement == "{INFLUX_MEASUREMENT}")
  |> filter(fn: (r) => {field_filter})
{chr(10).join(identity_filters)}
  |> aggregateWindow(every: 1m, fn: last, createEmpty: false)
  |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
'''

    with InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG) as client:
        query_api = client.query_api()
        result = query_api.query_data_frame(flux)

    frames: List[pd.DataFrame] = []
    if isinstance(result, list):
        frames = [df for df in result if isinstance(df, pd.DataFrame) and not df.empty]
    elif isinstance(result, pd.DataFrame) and not result.empty:
        frames = [result]

    if not frames:
        return []

    wide = pd.concat(frames, ignore_index=True)
    if wide.empty:
        return []

    if "_time" not in wide.columns:
        logger.warning("[rule-evaluate] risposta Influx senza colonna _time")
        return []

    # Filtri identitari tolleranti (se la colonna esiste).
    identity_filters = {
        "client_id": str(alert.client_id),
        "lampada": str(alert.lampada),
        "stanza": str(alert.stanza),
        "host": str(alert.host),
    }
    initial_rows = len(wide)
    for col, expected in identity_filters.items():
        if col in wide.columns:
            wide = wide[wide[col].astype(str) == expected]

    if wide.empty:
        logger.warning(
            "[rule-evaluate] nessuna riga dopo filtri identita. rows_before=%d cols=%s filters=%s",
            initial_rows,
            list(wide.columns),
            identity_filters,
        )
        return []

    wide = wide.rename(columns={"_time": "ts"})
    wide["ts"] = pd.to_datetime(wide["ts"], errors="coerce", utc=True)
    wide = wide.dropna(subset=["ts"]).sort_values("ts")
    if wide.empty:
        logger.warning("[rule-evaluate] tutte le righe hanno timestamp non valido")
        return []

    required = ["CO2", "VoC", "PMS2_5", "PMS10", "T", "H"]
    selected = pd.DataFrame({"ts": wide["ts"]})
    used_map: Dict[str, str] = {}
    for canonical in required:
        aliases = INFLUX_EVALUATE_FIELD_MAP.get(canonical, [])
        chosen = next((name for name in aliases if name in wide.columns), None)
        if chosen is not None:
            selected[canonical] = wide[chosen]
            used_map[chosen] = canonical
        else:
            selected[canonical] = pd.NA

    wide = selected
    logger.info("[rule-evaluate] mapping campi Influx -> canonici: %s", used_map)

    for col in required:
        if col in wide.columns:
            wide[col] = pd.to_numeric(wide[col], errors="coerce")

    # Con il profilo campi attuale VoC puo non essere presente: usa fallback neutro.
    if "VoC" not in wide.columns or wide["VoC"].isna().all():
        wide["VoC"] = 0.0
        logger.info("[rule-evaluate] VoC assente: fallback a 0.0")

    missing = sorted(set(required) - set(wide.columns))
    if missing:
        logger.warning(
            "[rule-evaluate] campi mancanti da Influx: %s | colonne disponibili: %s",
            ",".join(missing),
            list(wide.columns),
        )
        for col in missing:
            wide[col] = pd.NA

    # Stabilizza la serie nel caso in cui una metrica manchi in alcuni minuti.
    wide[required] = wide[required].ffill().bfill()

    clean = wide.dropna(subset=[c for c in required if c in wide.columns])
    if clean.empty:
        return []

    readings: List[RuleSensorReading] = []
    for _, row in clean.iterrows():
        readings.append(
            RuleSensorReading(
                ts=pd.Timestamp(row["ts"]).to_pydatetime().astimezone(timezone.utc).isoformat(),
                CO2=float(row["CO2"]),
                VoC=float(row["VoC"]),
                PMS2_5=float(row["PMS2_5"]),
                PMS10=float(row["PMS10"]),
                T=float(row["T"]),
                H=float(row["H"]),
            )
        )

    return readings


async def _query_influx_readings(alert: InfluxAlertEvent, lookback_minutes: int = 10) -> List[RuleSensorReading]:
    return await asyncio.to_thread(_query_influx_readings_blocking, alert, lookback_minutes)


def _apply_influx_level_override(scored: pd.DataFrame, evt: InfluxAlertEvent) -> pd.DataFrame:
    """Imposta lo stato da evento Influx: trigger=WARNING/CRITICAL, altri inquinanti=OK."""
    if scored.empty:
        return scored

    out = scored.copy()
    idx = out.index[-1]

    trigger_sev = {"WARNING": 1, "CRITICAL": 2}.get(str(evt.level).upper(), 0)
    trigger_pollutant = INFLUX_TRIGGER_TO_CANONICAL.get(evt.trigger_metrica)

    pollutants = ["CO2", "VoC", "PMS2_5", "PMS10"]
    for pollutant in pollutants:
        out[f"sev_{pollutant}"] = 0

    out["overall_severity"] = 0
    out["overall_status"] = "OK"
    out["dominant_pollutant"] = "NESSUNO"
    out["urgency"] = "bassa"

    if trigger_pollutant in pollutants and trigger_sev > 0:
        out.at[idx, f"sev_{trigger_pollutant}"] = int(trigger_sev)
        out.at[idx, "overall_severity"] = int(trigger_sev)
        out.at[idx, "overall_status"] = "TOLLERANZA" if trigger_sev == 1 else "ALERT"
        out.at[idx, "dominant_pollutant"] = trigger_pollutant
        out.at[idx, "urgency"] = "media" if trigger_sev == 1 else "alta"
    elif trigger_pollutant and trigger_sev > 0:
        out.at[idx, "overall_severity"] = int(trigger_sev)
        out.at[idx, "overall_status"] = "TOLLERANZA" if trigger_sev == 1 else "ALERT"
        out.at[idx, "dominant_pollutant"] = trigger_pollutant
        out.at[idx, "urgency"] = "media" if trigger_sev == 1 else "alta"

    logger.info(
        "[rule-evaluate] stato da Influx applicato: trigger=%s level=%s severity=%s",
        evt.trigger_metrica,
        evt.level,
        trigger_sev,
    )
    return out


def _apply_influx_action_fallback(scored: pd.DataFrame, evt: InfluxAlertEvent) -> pd.DataFrame:
    """Forza azioni statiche per metriche fuori dominio IA."""
    if scored.empty or evt.trigger_metrica in INFLUX_AI_ACTION_METRICS:
        return scored

    out = scored.copy()
    idx = out.index[-1]
    if evt.trigger_metrica in INFLUX_LUX_METRICS:
        action_code, action = INFLUX_LUX_ACTION
    else:
        action_code, action = INFLUX_FALLBACK_ACTION

    out.at[idx, "action_code"] = action_code
    out.at[idx, "recommended_action"] = action
    out.at[idx, "action_confidence"] = 0.75
    logger.info(
        "[rule-evaluate] azione fallback Influx applicata: trigger=%s action_code=%s",
        evt.trigger_metrica,
        action_code,
    )
    return out


def _run_rule_engine_from_influx_alert(df: pd.DataFrame, evt: InfluxAlertEvent) -> pd.DataFrame:
    """Pipeline per eventi Influx senza ricalcolo soglie su inquinanti."""
    out = add_dynamics(df)
    out["source"] = infer_source(out)
    out = add_trajectory_phase(out)
    out = _apply_influx_level_override(out, evt)
    out = recommend_actions(out)
    out = _apply_influx_action_fallback(out, evt)
    return out


@app.post("/api/rule/evaluate", response_model=RuleEvaluateLatestResponse)
async def evaluate(payload: Dict[str, Any]) -> RuleEvaluateLatestResponse:
    """Valuta dati sensore da payload diretto o da evento alert Influx + fetch storico backend."""
    site = "GENERIC"
    room = "GENERIC"
    logger.info("[rule-evaluate][payload] %s", payload)

    try:
        if "readings" in payload:
            req = RuleEvaluateRequest.model_validate(payload)
            df = _to_dataframe(site, room, req.readings)
        else:
            evt = InfluxAlertEvent.model_validate(payload)
            readings = await _query_influx_readings(evt, lookback_minutes=10)
            if not readings:
                detail = (
                    "Nessuna serie valida da Influx negli ultimi 10 minuti. "
                    f"Filtri: client_id={evt.client_id}, lampada={evt.lampada}, "
                    f"stanza={evt.stanza}, host={evt.host}."
                )
                logger.warning("[rule-evaluate] %s", detail)
                raise HTTPException(
                    status_code=422,
                    detail=detail,
                )

            site = evt.client_id
            room = f"{evt.stanza}:{evt.lampada}:{evt.host}"
            df = _to_dataframe(site, room, readings)

        if "readings" in payload:
            scored = run_rule_engine(df)
        else:
            scored = _run_rule_engine_from_influx_alert(df, evt)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[rule-evaluate] errore non gestito")
        raise HTTPException(status_code=422, detail=str(exc))

    if scored.empty:
        logger.warning("[rule-evaluate] run_rule_engine ha prodotto dataframe vuoto")
        raise HTTPException(status_code=422, detail="Nessun dato processabile.")

    latest_row = scored.iloc[-1]

    latest = {
        "overall_status": latest_row.get("overall_status"),
        "overall_severity": int(latest_row.get("overall_severity", 0)),
        "dominant_pollutant": latest_row.get("dominant_pollutant"),
        "trajectory_phase": latest_row.get("trajectory_phase"),
        "trajectory_roc": float(latest_row.get("trajectory_roc", 0.0)),
        "trajectory_acc": float(latest_row.get("trajectory_acc", 0.0)),
        "ttt_minutes": (
            None
            if pd.isna(latest_row.get("ttt_minutes"))
            else round(float(latest_row.get("ttt_minutes")), 2)
        ),
        "recommended_action": latest_row.get("recommended_action"),
        "action_code": latest_row.get("action_code"),
        "urgency": latest_row.get("urgency"),
        "action_due_minutes": (
            None
            if pd.isna(latest_row.get("action_due_minutes"))
            else int(float(latest_row.get("action_due_minutes")))
        ),
        "action_confidence": round(float(latest_row.get("action_confidence", 0.0)), 3),
    }
    if "readings" not in payload:
        trigger_pollutant = INFLUX_TRIGGER_TO_CANONICAL.get(evt.trigger_metrica)
        if trigger_pollutant:
            latest["trigger_pollutant"] = trigger_pollutant
            trigger_roc = latest_row.get(f"{trigger_pollutant}_roc", 0.0)
            trigger_acc = latest_row.get(f"{trigger_pollutant}_acc", 0.0)
            latest["trigger_roc"] = 0.0 if pd.isna(trigger_roc) else float(trigger_roc)
            latest["trigger_acc"] = 0.0 if pd.isna(trigger_acc) else float(trigger_acc)

    if "readings" not in payload:
        try:
            if not evt.predictive_only:
                await _send_alert_notifications(evt, latest)
            predictive_latest = _build_predictive_latest(evt, latest)
            if predictive_latest is not None:
                await _send_alert_notifications(evt, predictive_latest)
            elif evt.predictive_only:
                logger.info("[rule-evaluate] nessuna predizione inviata per payload predictive_only")
        except Exception as exc:
            logger.exception("[rule-evaluate] invio notifiche fallito: %s", exc)

    response = RuleEvaluateLatestResponse(**latest)
    #logger.info("[rule-evaluate][response] %s", response.model_dump())
    return response
