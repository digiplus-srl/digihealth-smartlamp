"""Modulo 2 — Servizio Advisory.

Analizza i risultati del forecasting rispetto alle soglie D4.3
e genera consigli proattivi usando le regole della Fase 2.

Flow:
  sensor readings  ──► Forecaster.predict() ──► Advisor.advise()
                                                  ├─ valuta stato attuale
                                                  ├─ identifica futuri superamenti
                                                  ├─ inferisce sorgente dalla traiettoria
                                                  └─ genera raccomandazioni azionabili
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.config import THRESHOLDS, UNITS, NAMES

# ── mappatura azioni per sorgente × inquinante (da Fase 2) ─────────
_ACTION_MAP = {
    # (source, dominant_pollutant) → azione consigliata
    ("cottura_attiva", "PMS2_5"):  "Accendi l'aspiratore e chiudi la porta della cucina per limitare la propagazione.",
    ("cottura_attiva", "PMS10"):   "Accendi l'aspiratore e chiudi la porta della cucina per limitare la propagazione.",
    ("cottura_attiva", "VoC"):     "Accendi l'aspiratore e apri brevemente la finestra (5-10 min) per ricambio rapido.",
    ("cottura_attiva", "CO2"):     "Aspiratore + apertura finestra breve per ridurre accumulo CO₂ da combustione.",
    ("occupazione_alta", "CO2"):   "Apri la finestra per 5-8 minuti per abbassare la CO₂.",
    ("occupazione_alta", "VoC"):   "Ventila l'ambiente: apri la finestra o attiva la ventilazione meccanica.",
    ("ac_split_senza_ricambio", "CO2"): "L'aria condizionata non garantisce ricambio. Apri la finestra 5 min.",
    ("ac_split_senza_ricambio", "VoC"): "L'aria condizionata non garantisce ricambio. Attiva la ventilazione.",
    ("ventilazione_insufficiente", "CO2"):  "Aumenta il ricambio d'aria: apri la finestra o accendi la ventilazione.",
    ("ventilazione_insufficiente", "VoC"):  "Aumenta il ricambio d'aria: apri la finestra o accendi la ventilazione.",
    ("ventilazione_insufficiente", "PMS2_5"): "Aumenta la ventilazione per ridurre il particolato fine.",
    ("ventilazione_insufficiente", "PMS10"):  "Aumenta la ventilazione per ridurre il particolato.",
    ("propagazione_da_stanza_adiacente", "PMS2_5"): "Isola la sorgente (chiudi la porta) e ventila localmente.",
    ("propagazione_da_stanza_adiacente", "PMS10"):  "Isola la sorgente (chiudi la porta) e ventila localmente.",
    ("finestra_aperta_o_ventilazione", None): "Ventilazione in corso. Mantieni finché i valori rientrano sotto soglia.",
}

# Azioni di fallback per inquinante
_FALLBACK_ACTION = {
    "CO2":    "Apri la finestra o attiva la ventilazione per ridurre la CO₂.",
    "VoC":    "Ventila l'ambiente per abbassare i VOC. Se possibile, rimuovi la sorgente.",
    "PMS2_5": "Attiva un purificatore d'aria o ventila brevemente per ridurre PM2.5.",
    "PMS10":  "Ventila o attiva un purificatore per ridurre il particolato PM10.",
}


class Advisor:
    """Analizza forecast + stato attuale e genera raccomandazioni."""

    BASE_POLLUTANTS = ["CO2", "VoC", "PMS2_5", "PMS10"]

    def advise(self, forecast_result: Dict) -> Dict:
        """Genera l'analisi completa con consigli.

        Parameters
        ----------
        forecast_result : dict
            Output di Forecaster.predict(). Contiene 'current' e 'forecast'.

        Returns
        -------
        dict con:
            current_status : stato attuale per ogni inquinante
            alerts         : lista di soglie che verranno superate nel futuro
            recommendations: lista di azioni consigliate ordinate per urgenza
            forecast       : ripasso dei dati di previsione
        """
        current = forecast_result["current"]
        forecast = forecast_result["forecast"]

        current_status = self._evaluate_status(current)
        source = self._infer_source(current)
        trajectory = self._classify_trajectory(current)
        alerts = self._find_future_alerts(current, forecast)
        recommendations = self._build_recommendations(
            current_status, source, trajectory, alerts
        )

        return {
            "current_status": current_status,
            "source": source,
            "trajectory": trajectory,
            "alerts": alerts,
            "recommendations": recommendations,
            "forecast": forecast,
        }

    # ── stato attuale ──────────────────────────────────────────────────
    def _evaluate_status(self, current: Dict) -> Dict:
        overall_severity = 0
        pollutant_status = {}

        for poll in self.BASE_POLLUTANTS:
            val = current.get(poll)
            if val is None:
                continue
            th = THRESHOLDS.get(poll, {})
            tol = th.get("tolleranza", float("inf"))
            alert = th.get("alert", float("inf"))

            if val >= alert:
                status = "ALERT"
                severity = 2
            elif val >= tol:
                status = "TOLLERANZA"
                severity = 1
            else:
                status = "OK"
                severity = 0

            overall_severity = max(overall_severity, severity)

            pollutant_status[poll] = {
                "value": round(val, 2),
                "unit": UNITS.get(poll, ""),
                "name": NAMES.get(poll, poll),
                "status": status,
                "soglia_tolleranza": tol,
                "soglia_alert": alert,
            }

        status_map = {0: "OK", 1: "TOLLERANZA", 2: "ALERT"}
        return {
            "overall": status_map[overall_severity],
            "overall_severity": overall_severity,
            "pollutants": pollutant_status,
        }

    # ── sorgente inferita dalla traiettoria attuale ────────────────────
    def _infer_source(self, current: Dict) -> str:
        """Inferisci la sorgente dalla dinamica attuale (roc dai raw)."""
        # Se le letture includono roc pre-calcolati li usiamo,
        # altrimenti la sorgente rimane indefinita.
        co2r = current.get("CO2_roc", 0)
        vocr = current.get("VoC_roc", 0)
        pmr  = current.get("PMS2_5_roc", 0)
        tr   = current.get("T_roc", 0)

        if pmr > 1.5 and vocr > 2.0:
            return "cottura_attiva"
        if co2r > 2.0 and pmr < 1.0:
            return "occupazione_alta"
        if tr < -0.10 and co2r > 1.0 and vocr > 0.8:
            return "ac_split_senza_ricambio"
        if co2r < -3.0 and vocr < -2.0:
            return "finestra_aperta_o_ventilazione"
        if co2r > 0.8 and vocr > 0.4:
            return "ventilazione_insufficiente"
        if pmr > 0.8 and vocr > 0.6 and co2r < 0.6:
            return "propagazione_da_stanza_adiacente"
        return "indefinita"

    # ── traiettoria ────────────────────────────────────────────────────
    def _classify_trajectory(self, current: Dict) -> Dict:
        """Classifica la fase della traiettoria dai valori correnti."""
        roc_vals = {p: current.get(f"{p}_roc", 0) for p in self.BASE_POLLUTANTS}
        acc_vals = {p: current.get(f"{p}_acc", 0) for p in self.BASE_POLLUTANTS}

        # Inquinante dominante = quello con roc più alto sopra zero
        dominant = max(roc_vals, key=lambda p: roc_vals[p])
        roc = roc_vals[dominant]
        acc = acc_vals[dominant]

        if roc > 0.8 and acc > 0.15:
            phase = "accelerazione"
        elif roc > 0.8 and abs(acc) <= 0.15:
            phase = "crescita_costante"
        elif roc > 0.8 and acc < -0.15:
            phase = "decelerazione"
        elif roc < -0.8:
            phase = "discesa"
        else:
            phase = "plateau"

        return {
            "phase": phase,
            "dominant_pollutant": dominant if roc > 0.2 else "NESSUNO",
            "roc": round(roc, 3),
            "acc": round(acc, 3),
        }

    # ── alert futuri ───────────────────────────────────────────────────
    def _find_future_alerts(
        self, current: Dict, forecast: List[Dict]
    ) -> List[Dict]:
        """Trova i punti nel forecast dove un inquinante supera una soglia."""
        alerts = []

        for point in forecast:
            h = point["horizon_min"]
            for poll in self.BASE_POLLUTANTS:
                pred = point.get(poll)
                if pred is None:
                    continue

                th = THRESHOLDS.get(poll, {})
                tol = th.get("tolleranza", float("inf"))
                alert_th = th.get("alert", float("inf"))

                cur_val = current.get(poll, 0)

                # Controlla superamento alert
                if pred >= alert_th and cur_val < alert_th:
                    alerts.append({
                        "horizon_min": h,
                        "pollutant": poll,
                        "pollutant_name": NAMES.get(poll, poll),
                        "predicted_value": round(pred, 2),
                        "current_value": round(cur_val, 2),
                        "threshold_exceeded": "alert",
                        "threshold_value": alert_th,
                        "unit": UNITS.get(poll, ""),
                    })
                # Controlla superamento tolleranza
                elif pred >= tol and cur_val < tol:
                    alerts.append({
                        "horizon_min": h,
                        "pollutant": poll,
                        "pollutant_name": NAMES.get(poll, poll),
                        "predicted_value": round(pred, 2),
                        "current_value": round(cur_val, 2),
                        "threshold_exceeded": "tolleranza",
                        "threshold_value": tol,
                        "unit": UNITS.get(poll, ""),
                    })

        # Rimuovi duplicati: tieni solo il primo orizzonte per ogni (poll, threshold)
        seen = set()
        unique_alerts = []
        for a in alerts:
            key = (a["pollutant"], a["threshold_exceeded"])
            if key not in seen:
                seen.add(key)
                unique_alerts.append(a)

        return sorted(unique_alerts, key=lambda a: a["horizon_min"])

    # ── raccomandazioni ────────────────────────────────────────────────
    def _build_recommendations(
        self,
        current_status: Dict,
        source: str,
        trajectory: Dict,
        alerts: List[Dict],
    ) -> List[Dict]:
        """Costruisce raccomandazioni proattive ordinate per urgenza."""
        recs: List[Dict] = []

        # ① Azioni per alert FUTURI (proattive)
        for alert in alerts:
            poll = alert["pollutant"]
            h = alert["horizon_min"]
            level = alert["threshold_exceeded"]

            # Cerca azione specifica (source, pollutant)
            action = _ACTION_MAP.get((source, poll))
            if action is None:
                action = _FALLBACK_ACTION.get(poll, "Ventila l'ambiente.")

            urgency = "alta" if level == "alert" else "media"

            # Tempo suggerito per agire = 50% dell'orizzonte
            act_within = max(1, int(h * 0.5))

            reason = (
                f"{NAMES.get(poll, poll)} previsto a {alert['predicted_value']} "
                f"{UNITS.get(poll, '')} tra {h} min "
                f"(soglia {level}: {alert['threshold_value']} {UNITS.get(poll, '')})"
            )

            recs.append({
                "action": action,
                "urgency": urgency,
                "reason": reason,
                "pollutant": poll,
                "source": source,
                "act_within_minutes": act_within,
                "confidence": self._compute_confidence(source, trajectory),
            })

        # ② Azioni per stato attuale già in soglia
        for poll, info in current_status.get("pollutants", {}).items():
            if info["status"] == "OK":
                continue
            # Evita duplicati con alert futuri
            if any(r["pollutant"] == poll for r in recs):
                continue

            action = _ACTION_MAP.get((source, poll))
            if action is None:
                action = _FALLBACK_ACTION.get(poll, "Ventila l'ambiente.")

            urgency = "alta" if info["status"] == "ALERT" else "media"

            recs.append({
                "action": action,
                "urgency": urgency,
                "reason": (
                    f"{info['name']} attualmente a {info['value']} {info['unit']} "
                    f"— stato {info['status']}"
                ),
                "pollutant": poll,
                "source": source,
                "act_within_minutes": 0,
                "confidence": self._compute_confidence(source, trajectory),
            })

        # ③ Alert traiettoria in accelerazione senza soglia imminente
        if trajectory["phase"] == "accelerazione" and not recs:
            poll = trajectory["dominant_pollutant"]
            if poll != "NESSUNO":
                recs.append({
                    "action": "Trend in accelerazione: valuta ventilazione preventiva.",
                    "urgency": "bassa",
                    "reason": (
                        f"{NAMES.get(poll, poll)} in rapida crescita "
                        f"(roc={trajectory['roc']}, acc={trajectory['acc']})"
                    ),
                    "pollutant": poll,
                    "source": source,
                    "act_within_minutes": 5,
                    "confidence": self._compute_confidence(source, trajectory),
                })

        # ④ Se tutto OK
        if not recs:
            recs.append({
                "action": "Aria nella norma. Nessuna azione necessaria.",
                "urgency": "nessuna",
                "reason": "Tutti gli inquinanti sotto soglia, nessun superamento previsto.",
                "pollutant": None,
                "source": source,
                "act_within_minutes": None,
                "confidence": 0.90,
            })

        # Ordina: alta > media > bassa > nessuna
        priority = {"alta": 0, "media": 1, "bassa": 2, "nessuna": 3}
        recs.sort(key=lambda r: (priority.get(r["urgency"], 9), r.get("act_within_minutes") or 999))

        return recs

    @staticmethod
    def _compute_confidence(source: str, trajectory: Dict) -> float:
        """Confidenza euristica nella raccomandazione."""
        conf = 0.50
        if source != "indefinita":
            conf += 0.20
        if trajectory.get("dominant_pollutant", "NESSUNO") != "NESSUNO":
            conf += 0.10
        if trajectory.get("phase", "plateau") != "plateau":
            conf += 0.10
        return min(round(conf, 2), 0.95)
