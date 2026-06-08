"""API REST — Servizio AirQuality PowerSense.

Due modalità:
  POST /api/forecast  → Modalità 1: previsione multi-orizzonte
  POST /api/advise    → Modalità 2: previsione + consigli proattivi
  GET  /api/health    → Health check

Avvio:  uvicorn src.api:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.service.forecaster import Forecaster
from src.service.advisor import Advisor

# ── modelli di I/O ─────────────────────────────────────────────────────

class SensorReading(BaseModel):
    """Una singola lettura sensoriale (1 minuto)."""
    ts: Optional[str] = Field(None, description="Timestamp ISO-8601 (opzionale)")
    CO2: float = Field(..., ge=0, le=10000, description="CO₂ in ppm")
    VoC: float = Field(..., ge=0, le=5000, description="VOC in ppb")
    PMS2_5: float = Field(..., ge=0, le=1000, description="PM2.5 in µg/m³")
    PMS10: float = Field(..., ge=0, le=2000, description="PM10 in µg/m³")
    T: float = Field(..., ge=-20, le=60, description="Temperatura in °C")
    H: float = Field(..., ge=0, le=100, description="Umidità relativa %")


class ForecastRequest(BaseModel):
    """Richiesta per i due endpoint (forecast e advise)."""
    readings: List[SensorReading] = Field(
        ...,
        min_length=30,
        max_length=120,
        description="Almeno 30 letture ordinate cronologicamente (1/min)",
    )

    @field_validator("readings")
    @classmethod
    def check_min_readings(cls, v):
        if len(v) < 30:
            raise ValueError("Servono almeno 30 letture (ultimi 30 minuti).")
        return v


class ForecastPoint(BaseModel):
    horizon_min: int
    CO2: float
    CO2_ci95_lower: float
    CO2_ci95_upper: float
    VoC: float
    VoC_ci95_lower: float
    VoC_ci95_upper: float
    PMS2_5: float
    PMS2_5_ci95_lower: float
    PMS2_5_ci95_upper: float
    PMS10: float
    PMS10_ci95_lower: float
    PMS10_ci95_upper: float


class ForecastResponse(BaseModel):
    current: Dict
    forecast: List[ForecastPoint]


class AdviseResponse(BaseModel):
    current_status: Dict
    source: str
    trajectory: Dict
    alerts: List[Dict]
    recommendations: List[Dict]
    forecast: List[ForecastPoint]


# ── lifecycle ──────────────────────────────────────────────────────────

_forecaster: Optional[Forecaster] = None
_advisor: Optional[Advisor] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _forecaster, _advisor
    _forecaster = Forecaster()
    _advisor = Advisor()
    yield


app = FastAPI(
    title="AirQuality PowerSense API",
    version="1.0.0",
    description=(
        "Servizio di previsione della qualità dell'aria indoor con consigli proattivi.\n\n"
        "**Modalità 1** (`/api/forecast`): previsione dei livelli inquinanti a +5/+15/+30/+60 min.\n\n"
        "**Modalità 2** (`/api/advise`): previsione + analisi soglie + raccomandazioni azionabili."
    ),
    lifespan=lifespan,
)


# ── helper ─────────────────────────────────────────────────────────────

def _readings_to_dicts(readings: List[SensorReading]) -> List[Dict]:
    """Converte le letture Pydantic in dicts e calcola roc/acc per l'advisory."""
    dicts = [r.model_dump() for r in readings]
    n = len(dicts)

    # Calcola roc e acc sull'ultima lettura (per l'advisor)
    if n >= 2:
        for key in ["CO2", "VoC", "PMS2_5", "PMS10", "T", "H"]:
            dicts[-1][f"{key}_roc"] = dicts[-1][key] - dicts[-2][key]
    if n >= 3:
        for key in ["CO2", "VoC", "PMS2_5", "PMS10", "T", "H"]:
            roc_now = dicts[-1][key] - dicts[-2][key]
            roc_prev = dicts[-2][key] - dicts[-3][key]
            dicts[-1][f"{key}_acc"] = roc_now - roc_prev

    return dicts


# ── endpoints ──────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    """Health check."""
    return {"status": "ok", "model": "lstm_v5_multihorizon"}


@app.post("/api/forecast", response_model=ForecastResponse)
async def forecast(req: ForecastRequest):
    """**Modalità 1 — Forecasting puro.**

    Riceve le ultime 30+ letture sensoriali (1/min) e restituisce
    le previsioni dei 4 inquinanti a +5, +15, +30, +60 minuti.
    """
    dicts = _readings_to_dicts(req.readings)
    try:
        result = _forecaster.predict(dicts)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))
    return result


@app.post("/api/advise", response_model=AdviseResponse)
async def advise(req: ForecastRequest):
    """**Modalità 2 — Forecasting + Consigli proattivi.**

    Riceve le ultime 30+ letture, esegue il forecasting, analizza
    i risultati rispetto alle soglie D4.3, e genera raccomandazioni
    operative basate sulle regole della Fase 2.
    """
    dicts = _readings_to_dicts(req.readings)
    try:
        forecast_result = _forecaster.predict(dicts)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Arricchisci current con roc/acc per l'advisor
    last = dicts[-1]
    for key in ["CO2_roc", "VoC_roc", "PMS2_5_roc", "PMS10_roc", "T_roc", "H_roc",
                "CO2_acc", "VoC_acc", "PMS2_5_acc", "PMS10_acc", "T_acc", "H_acc"]:
        if key in last:
            forecast_result["current"][key] = last[key]

    advisory = _advisor.advise(forecast_result)
    return advisory
