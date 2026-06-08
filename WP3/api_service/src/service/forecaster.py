"""Modulo 1 — Servizio di Forecasting.

Carica il modello LSTM V5b + scaler e fa inferenza su una finestra
di 30 letture sensoriali (1 lettura/minuto).

Restituisce le previsioni per CO2, VoC, PMS2_5, PMS10
ai 4 orizzonti: +5, +15, +30, +60 minuti.
"""
from __future__ import annotations

import csv
import json
import os
import tempfile
import base64
import marshal
from typing import Dict, List
import zipfile

import numpy as np
import joblib
import tensorflow as tf

# ── paths ──────────────────────────────────────────────────────────────
_MODEL_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "output", "phase3_v5",
)
_MODEL_DIR = os.getenv("WP3_MODEL_DIR", _MODEL_DIR)


def _runtime_last_step_lambda_code() -> str:
    """Build Lambda bytecode compatible with the running Python version."""

    last_step = lambda ts: ts[:, -1, :]
    return base64.encodebytes(marshal.dumps(last_step.__code__)).decode("ascii")


def _patch_lambda_compat(model_config: Dict) -> bool:
    layers = model_config.get("config", {}).get("layers", [])
    changed = False

    for layer in layers:
        if layer.get("class_name") != "Lambda":
            continue
        layer_config = layer.setdefault("config", {})
        if layer_config.get("name") != "last_step":
            continue

        # Rewrite serialized Lambda code using current Python runtime bytecode.
        # This prevents "unknown opcode" errors when model was saved with
        # a different Python minor version.
        function = layer_config.setdefault("function", {"class_name": "__lambda__", "config": {}})
        function_config = function.setdefault("config", {})
        runtime_code = _runtime_last_step_lambda_code()
        if function_config.get("code") != runtime_code:
            function_config["code"] = runtime_code
            function_config["defaults"] = None
            function_config["closure"] = None
            changed = True

        if "output_shape" in layer_config:
            pass
        else:
            layer_config["output_shape"] = [25]
            changed = True

    return changed


def _build_patched_model_archive(model_path: str) -> str:
    temp_file = tempfile.NamedTemporaryFile(suffix=".keras", delete=False)
    temp_file.close()
    patched = False

    with zipfile.ZipFile(model_path, "r") as source_zip:
        with zipfile.ZipFile(temp_file.name, "w", compression=zipfile.ZIP_DEFLATED) as target_zip:
            for entry in source_zip.infolist():
                payload = source_zip.read(entry.filename)
                if entry.filename == "config.json":
                    model_config = json.loads(payload.decode("utf-8"))
                    patched = _patch_lambda_compat(model_config)
                    payload = json.dumps(model_config).encode("utf-8")
                target_zip.writestr(entry, payload)

    if not patched:
        os.unlink(temp_file.name)
        return model_path

    return temp_file.name


def _load_model_compat(model_path: str):
    patched_path = _build_patched_model_archive(model_path)
    path_to_load = patched_path
    try:
        return tf.keras.models.load_model(path_to_load, compile=False, safe_mode=False)
    finally:
        if patched_path != model_path and os.path.exists(patched_path):
            os.unlink(patched_path)


class Forecaster:
    """Carica modello + scaler una volta e offre predict() ripetute."""

    SENSOR_COLS = ["CO2", "VoC", "PMS2_5", "PMS10", "T", "H"]
    BASE_POLLUTANTS = ["CO2", "VoC", "PMS2_5", "PMS10"]
    HORIZONS = [5, 15, 30, 60]
    LOOKBACK = 30  # minuti di storico necessari

    def __init__(self, model_dir: str = _MODEL_DIR):
        best = os.path.join(model_dir, "lstm_v5_best.keras")
        fallback = os.path.join(model_dir, "lstm_v5_model.keras")
        model_path = best if os.path.exists(best) else fallback
        self._model = _load_model_compat(model_path)
        self._f_scaler = joblib.load(os.path.join(model_dir, "feature_scaler.pkl"))
        self._t_scaler = joblib.load(os.path.join(model_dir, "target_scaler.pkl"))

        with open(os.path.join(model_dir, "service_meta.json")) as f:
            self._meta = json.load(f)

        # Carica RMSE per ogni (orizzonte, inquinante) per il calcolo CI95
        metrics_path = os.path.join(model_dir, "phase3_v5_metrics_by_horizon.csv")
        self._rmse: Dict[tuple, float] = {}
        with open(metrics_path, newline="") as f:
            for row in csv.DictReader(f):
                key = (int(row["horizon_min"]), row["pollutant"])
                self._rmse[key] = float(row["rmse"])

    # ── public API ─────────────────────────────────────────────────────
    def predict(self, readings: List[Dict[str, float]]) -> Dict:
        """Esegue forecasting a partire dalle ultime 30 letture.

        Parameters
        ----------
        readings : list[dict]
            30 dizionari ordinati cronologicamente, ognuno con almeno:
            CO2, VoC, PMS2_5, PMS10, T, H  (e opzionalmente 'ts').

        Returns
        -------
        dict con chiavi:
            current  : valori attuali (ultima lettura)
            forecast : lista di 4 dict (uno per orizzonte)
        """
        if len(readings) < self.LOOKBACK:
            raise ValueError(
                f"Servono almeno {self.LOOKBACK} letture (1/min). "
                f"Ricevute: {len(readings)}"
            )

        # Usa solo le ultime LOOKBACK letture
        window = readings[-self.LOOKBACK:]

        # Costruisci matrice raw (30 × 6)
        raw = np.array(
            [[r[c] for c in self.SENSOR_COLS] for r in window],
            dtype=np.float32,
        )

        # Calcoli derivati: roc e acc per i 4 inquinanti
        roc = np.zeros((len(window), 4), dtype=np.float32)
        acc = np.zeros((len(window), 4), dtype=np.float32)
        for j in range(4):  # CO2, VoC, PMS2_5, PMS10
            vals = raw[:, j]
            roc[1:, j] = np.diff(vals)
            acc[2:, j] = np.diff(roc[:, j])[1:]

        # Rolling means a 5 e 10 minuti per i 4 inquinanti
        rm5 = np.zeros((len(window), 4), dtype=np.float32)
        rm10 = np.zeros((len(window), 4), dtype=np.float32)
        for j in range(4):
            vals = raw[:, j]
            for i in range(len(window)):
                start5 = max(0, i - 4)
                rm5[i, j] = vals[start5:i + 1].mean()
                start10 = max(0, i - 9)
                rm10[i, j] = vals[start10:i + 1].mean()

        # Feature temporali
        ts_hint = window[-1].get("ts")
        if ts_hint is not None:
            import pandas as pd
            t = pd.Timestamp(ts_hint)
            hour = t.hour + t.minute / 60.0
            weekend = float(t.dayofweek >= 5)
        else:
            hour = 12.0
            weekend = 0.0
        hour_sin = np.sin(2 * np.pi * hour / 24.0)
        hour_cos = np.cos(2 * np.pi * hour / 24.0)

        temporal = np.full((len(window), 3), [hour_sin, hour_cos, weekend], dtype=np.float32)

        # Feature matrix (30 × 25)
        features = np.concatenate([raw, roc, acc, rm5, rm10, temporal], axis=1)

        # Scala con feature scaler
        features_scaled = self._f_scaler.transform(features)
        X = features_scaled.reshape(1, self.LOOKBACK, -1)

        # Inferenza
        y_scaled = self._model.predict(X, verbose=0)
        y_transformed = self._t_scaler.inverse_transform(y_scaled)
        y_real = self._inverse_targets(y_transformed)

        # Costruisci output
        current = {c: float(raw[-1, i]) for i, c in enumerate(self.SENSOR_COLS)}

        forecast = []
        z95 = 1.96  # z-score per il 95% CI (distribuzione Gaussiana)
        for h_idx, h in enumerate(self.HORIZONS):
            base = h_idx * 4
            point: Dict[str, object] = {"horizon_min": h}
            for p_idx, p in enumerate(self.BASE_POLLUTANTS):
                val = round(float(y_real[0, base + p_idx]), 2)
                rmse = self._rmse.get((h, p), 0.0)
                half = round(z95 * rmse, 2)
                point[p] = val
                point[f"{p}_ci95_lower"] = round(max(0.0, val - half), 2)
                point[f"{p}_ci95_upper"] = round(val + half, 2)
            forecast.append(point)

        return {"current": current, "forecast": forecast}

    # ── internals ──────────────────────────────────────────────────────
    @staticmethod
    def _inverse_targets(Y: np.ndarray) -> np.ndarray:
        """Inverte log1p su VoC, PMS2_5, PMS10 per ogni blocco orizzonte."""
        out = Y.copy().astype(np.float32)
        n_horizons = 4
        for block in range(n_horizons):
            base = block * 4
            for idx in [1, 2, 3]:  # VoC, PMS2_5, PMS10
                col = base + idx
                out[:, col] = np.expm1(out[:, col])
        return np.clip(out, 0, None)
