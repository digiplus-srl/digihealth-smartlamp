"""FASE 2 - Rule-based engine (Task 3.1).

Output principali:
- stato per minuto: OK / TOLLERANZA / ALERT
- sorgente stimata (euristica) dalla dinamica delle curve
- raccomandazione contestuale
- time-to-threshold (TTT) per inquinante dominante
"""

from __future__ import annotations

import os
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import THRESHOLDS, KEY_POLLUTANTS


STATUS_MAP = {0: "OK", 1: "TOLLERANZA", 2: "ALERT"}


def _severity_simple(series: pd.Series, tol: float, alert: float) -> pd.Series:
    return pd.Series(
        np.where(series >= alert, 2, np.where(series >= tol, 1, 0)),
        index=series.index,
        dtype="int8",
    )


def _severity_range(series: pd.Series, tol_low: float, tol_high: float, alert_low: float, alert_high: float) -> pd.Series:
    return pd.Series(
        np.where(
            (series < alert_low) | (series > alert_high),
            2,
            np.where((series < tol_low) | (series > tol_high), 1, 0),
        ),
        index=series.index,
        dtype="int8",
    )


def add_dynamics(df: pd.DataFrame) -> pd.DataFrame:
    """Aggiunge rate-of-change a 1 minuto per sensori chiave."""
    result = df.copy()
    group = result.groupby(["site", "room"], group_keys=False)
    for col in ["CO2", "VoC", "PMS2_5", "PMS10", "T", "H"]:
        if col in result.columns:
            result[f"{col}_roc"] = group[col].diff()
            result[f"{col}_acc"] = group[f"{col}_roc"].diff()
    return result


def add_trajectory_phase(df: pd.DataFrame) -> pd.DataFrame:
    """Classifica la fase della traiettoria in base a roc + accelerazione.

    Fasi:
    - accelerazione: roc > 0 e acc > 0
    - crescita_costante: roc > 0 e |acc| piccolo
    - decelerazione: roc > 0 e acc < 0
    - discesa: roc < 0
    - plateau: variazioni piccole
    """
    out = df.copy()

    dominant_pollutant, _ = _dominant_exceedance(out)
    out["dominant_pollutant"] = dominant_pollutant

    roc = pd.Series(np.nan, index=out.index, dtype=float)
    acc = pd.Series(np.nan, index=out.index, dtype=float)

    for poll, roc_col, acc_col in [
        ("CO2", "CO2_roc", "CO2_acc"),
        ("VoC", "VoC_roc", "VoC_acc"),
        ("PMS2_5", "PMS2_5_roc", "PMS2_5_acc"),
        ("PMS10", "PMS10_roc", "PMS10_acc"),
    ]:
        mask = out["dominant_pollutant"] == poll
        if roc_col in out.columns:
            roc.loc[mask] = out.loc[mask, roc_col].astype(float)
        if acc_col in out.columns:
            acc.loc[mask] = out.loc[mask, acc_col].astype(float)

    roc = roc.fillna(0)
    acc = acc.fillna(0)

    conds = [
        (roc > 0.8) & (acc > 0.15),
        (roc > 0.8) & (acc >= -0.15) & (acc <= 0.15),
        (roc > 0.8) & (acc < -0.15),
        (roc < -0.8),
    ]
    labels = ["accelerazione", "crescita_costante", "decelerazione", "discesa"]
    out["trajectory_phase"] = np.select(conds, labels, default="plateau")
    out["trajectory_roc"] = roc
    out["trajectory_acc"] = acc
    return out


def infer_source(df: pd.DataFrame) -> pd.Series:
    """Stima la sorgente di inquinamento con euristiche sulle curve."""
    co2r = df.get("CO2_roc", pd.Series(0, index=df.index)).fillna(0)
    vocr = df.get("VoC_roc", pd.Series(0, index=df.index)).fillna(0)
    pmr = df.get("PMS2_5_roc", pd.Series(0, index=df.index)).fillna(0)
    tr = df.get("T_roc", pd.Series(0, index=df.index)).fillna(0)

    conds = [
        # Picco emissivo interno: PM + VOC crescono insieme rapidamente
        (pmr > 1.5) & (vocr > 2.0),
        # Accumulo occupazione: CO2 sale in modo sostenuto
        (co2r > 2.0) & (pmr < 1.0),
        # AC split senza ricambio: temperatura scende ma CO2/VOC salgono
        (tr < -0.10) & (co2r > 1.0) & (vocr > 0.8),
        # Finestra aperta / ventilazione efficace: calo multi-inquinante
        (co2r < -3.0) & (vocr < -2.0),
        # Ventilazione insufficiente: salita lenta ma persistente
        (co2r > 0.8) & (vocr > 0.4),
        # Propagazione da stanza adiacente: PM sale senza forte CO2 locale
        (pmr > 0.8) & (vocr > 0.6) & (co2r < 0.6),
    ]
    labels = [
        "picco_emissivo_interno",
        "occupazione_alta",
        "ac_split_senza_ricambio",
        "finestra_aperta_o_ventilazione",
        "ventilazione_insufficiente",
        "propagazione_da_stanza_adiacente",
    ]
    return pd.Series(np.select(conds, labels, default="indefinita"), index=df.index)


def compute_status(df: pd.DataFrame) -> pd.DataFrame:
    """Calcola severità per inquinante e stato complessivo."""
    out = df.copy()

    severity_cols: List[str] = []

    for col, th in THRESHOLDS.items():
        if col not in out.columns:
            continue
        sev_col = f"sev_{col}"
        if "tolleranza" in th and "alert" in th:
            out[sev_col] = _severity_simple(out[col], th["tolleranza"], th["alert"])
            severity_cols.append(sev_col)
        elif "tolleranza_low" in th:
            out[sev_col] = _severity_range(
                out[col],
                th["tolleranza_low"],
                th["tolleranza_high"],
                th["alert_low"],
                th["alert_high"],
            )
            severity_cols.append(sev_col)

    if not severity_cols:
        out["overall_severity"] = 0
    else:
        out["overall_severity"] = out[severity_cols].max(axis=1).astype("int8")

    out["overall_status"] = out["overall_severity"].map(STATUS_MAP)
    return out


def _dominant_exceedance(df: pd.DataFrame) -> Tuple[pd.Series, pd.DataFrame]:
    """Inquinante dominante in base all'exceedance normalizzata sopra tolleranza."""
    ex = pd.DataFrame(index=df.index)
    for col in KEY_POLLUTANTS:
        if col not in df.columns or col not in THRESHOLDS:
            continue
        tol = THRESHOLDS[col]["tolleranza"]
        alert = THRESHOLDS[col]["alert"]
        span = max(alert - tol, 1e-9)
        ex[col] = ((df[col] - tol) / span).clip(lower=0)

    if ex.shape[1] == 0:
        return pd.Series("NA", index=df.index), ex

    ex = ex.fillna(0)
    dominant = ex.idxmax(axis=1)
    no_exceed = ex.max(axis=1) <= 0
    dominant = dominant.mask(no_exceed, "NESSUNO")
    return dominant, ex


def _compute_ttt(df: pd.DataFrame, dominant_pollutant: pd.Series) -> pd.Series:
    """Calcola tempo al prossimo threshold (minuti) per inquinante dominante."""
    ttt = pd.Series(np.nan, index=df.index, dtype=float)

    roc_map = {
        "CO2": "CO2_roc",
        "VoC": "VoC_roc",
        "PMS2_5": "PMS2_5_roc",
        "PMS10": "PMS10_roc",
    }

    for poll in ["CO2", "VoC", "PMS2_5", "PMS10"]:
        mask = dominant_pollutant == poll
        if not mask.any() or poll not in df.columns:
            continue

        th = THRESHOLDS[poll]
        current = df.loc[mask, poll]
        sev = df.loc[mask, "overall_severity"]
        target = np.where(sev == 0, th["tolleranza"], th["alert"])  # ok->tol, toll->alert

        roc_col = roc_map.get(poll)
        roc = df.loc[mask, roc_col] if roc_col in df.columns else pd.Series(np.nan, index=current.index)
        roc = roc.astype(float)

        valid = roc > 0
        vals = (target - current) / roc
        vals = vals.where(valid)
        vals = vals.clip(lower=0, upper=180)  # max 3h
        ttt.loc[mask] = vals

    return ttt


def recommend_actions(df: pd.DataFrame) -> pd.DataFrame:
    """Genera raccomandazioni contestuali in base a stato + sorgente."""
    out = df.copy()

    dominant_pollutant = out["dominant_pollutant"] if "dominant_pollutant" in out.columns else _dominant_exceedance(out)[0]
    out["dominant_pollutant"] = dominant_pollutant
    out["ttt_minutes"] = _compute_ttt(out, dominant_pollutant)

    src = out["source"].fillna("indefinita")
    sev = out["overall_severity"].fillna(0)
    phase = out.get("trajectory_phase", pd.Series("plateau", index=out.index))

    urgent = sev >= 2

    conds = [
        (sev == 0),
        (phase == "accelerazione") & (sev >= 1),
        (src == "picco_emissivo_interno") & (~urgent),
        (src == "picco_emissivo_interno") & urgent,
        (src == "occupazione_alta") & (~urgent),
        (src == "occupazione_alta") & urgent,
        (src == "ac_split_senza_ricambio"),
        (src == "ventilazione_insufficiente"),
        (src == "propagazione_da_stanza_adiacente"),
        (src == "finestra_aperta_o_ventilazione"),
    ]

    actions = [
        "Monitoraggio: nessuna azione immediata.",
        "Trend in accelerazione: intervieni subito con ventilazione preventiva.",
        "Rilevato picco emissivo interno: attiva ventilazione e limita propagazione dalla stanza sorgente.",
        "ALERT picco emissivo interno: ventilazione immediata + apertura finestra breve (5-10 min).",
        "Accumulo da occupazione: apri finestra 5-8 minuti.",
        "CO2 in ALERT: ventilazione immediata (finestra o ricambio meccanico).",
        "AC split senza ricambio: apri finestra 5 minuti per ricambio aria.",
        "Ventilazione insufficiente: aumenta ricambio aria (finestra/ventilazione).",
        "Propagazione da stanza adiacente: isola la sorgente e aumenta ventilazione locale.",
        "Ventilazione in corso: mantieni azione fino al rientro sotto soglia.",
    ]

    action_codes = {
        "Monitoraggio: nessuna azione immediata.": "WP3_01",
        "Trend in accelerazione: intervieni subito con ventilazione preventiva.": "WP3_02",
        "Rilevato picco emissivo interno: attiva ventilazione e limita propagazione dalla stanza sorgente.": "WP3_03",
        "ALERT picco emissivo interno: ventilazione immediata + apertura finestra breve (5-10 min).": "WP3_04",
        "Accumulo da occupazione: apri finestra 5-8 minuti.": "WP3_05",
        "CO2 in ALERT: ventilazione immediata (finestra o ricambio meccanico).": "WP3_06",
        "AC split senza ricambio: apri finestra 5 minuti per ricambio aria.": "WP3_07",
        "Ventilazione insufficiente: aumenta ricambio aria (finestra/ventilazione).": "WP3_08",
        "Propagazione da stanza adiacente: isola la sorgente e aumenta ventilazione locale.": "WP3_09",
        "Ventilazione in corso: mantieni azione fino al rientro sotto soglia.": "WP3_10",
    }

    out["recommended_action"] = np.select(
        conds,
        actions,
        default="Intervento lieve:  aumenta il ricambio d’aria e verifica l’andamento nei prossimi minuti.",
    )
    out["action_code"] = out["recommended_action"].map(action_codes).fillna("WP3_11")

    out["urgency"] = np.where(out["overall_severity"] >= 2, "alta", np.where(out["overall_severity"] == 1, "media", "bassa"))

    # timing consigliato dell'azione
    out["action_due_minutes"] = np.where(
        out["ttt_minutes"].notna(),
        np.maximum(np.floor(out["ttt_minutes"] * 0.5), 0),
        np.where(out["overall_severity"] >= 2, 0, np.nan),
    )
    out.loc[out["trajectory_phase"] == "accelerazione", "action_due_minutes"] = 0

    # confidenza euristica (da raffinare in Fase 3)
    conf = np.full(len(out), 0.45, dtype=float)
    conf += np.where(out["source"] != "indefinita", 0.20, 0.0)
    conf += np.where(out["dominant_pollutant"] != "NESSUNO", 0.15, 0.0)
    conf += np.where(out["trajectory_phase"] != "plateau", 0.10, 0.0)
    out["action_confidence"] = np.clip(conf, 0.0, 0.95)

    return out


def run_rule_engine(df: pd.DataFrame) -> pd.DataFrame:
    """Pipeline completa Fase 2 rule-based."""
    out = add_dynamics(df)
    out = compute_status(out)
    out["source"] = infer_source(out)
    out = add_trajectory_phase(out)
    out = recommend_actions(out)
    return out
