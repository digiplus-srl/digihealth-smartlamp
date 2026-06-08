import time
import threading
import numpy as np
from collections import deque
from typing import Dict, Any
from ..logger import logger

CALIBRATION_SECS = 10   # durata fase calibrazione secondi


class AudioComfortProcessor:
    """
    Gestisce le fasi:
      IDLE      → sistema fermo
      CALIBRATING → misura il rumore di fondo e calcola le soglie
      CHECK     → ascolta per check_duration secondi
      COMFORT   → emette rumore bianco per comfort_duration secondi
    """

    def __init__(self, config: Dict[str, Any]):
        self.check_duration   = config.get('check_duration',   30)
        self.comfort_duration = config.get('comfort_duration', 300)
        self.th_tol           = float(config.get('tolerance_threshold', 45.0))
        self.th_crit          = float(config.get('critical_threshold',  65.0))

        self._lock = threading.Lock()
        self._state = {
            "active":        False,
            "mode":          "IDLE",   # IDLE | CALIBRATING | CHECK | COMFORT
            "countdown":     0,
            "needs_comfort": False,
            "th_tol":        self.th_tol,
            "th_crit":       self.th_crit,
            "volume":        0.5,
        }
        self._logic_thread: threading.Thread | None = None
        self._audio_out_thread: threading.Thread | None = None
        # callback per output audio – impostato da WebManager
        self._on_comfort_tick = None

    # ------------------------------------------------------------------
    # API pubblica chiamata da WebManager
    # ------------------------------------------------------------------
    def toggle(self):
        with self._lock:
            active = not self._state["active"]
            self._state["active"] = active
            if active:
                self._state["mode"] = "CHECK"
                self._state["needs_comfort"] = False
                self._start_logic_thread()
            else:
                self._state["mode"] = "IDLE"
                self._state["countdown"] = 0
        logger.info(f"AudioComfort toggle → {'ON' if active else 'OFF'}")

    def calibrate(self, db_samples: list) -> Dict[str, Any]:
        """
        Riceve una lista di campioni dB registrati durante la calibrazione,
        calcola le soglie e le salva nello stato.
        """
        if not db_samples:
            return {"error": "Nessun campione audio ricevuto"}

        arr = np.array(db_samples, dtype=float)
        avg = float(np.mean(arr))
        std = float(np.std(arr))

        new_tol  = round(avg + std * 1.5, 1)
        new_crit = round(avg + std * 3.0, 1)

        with self._lock:
            self._state["th_tol"]  = new_tol
            self._state["th_crit"] = new_crit
            self._state["mode"]    = "IDLE"

        logger.info(f"Calibrazione: avg={avg:.1f} std={std:.1f} → tol={new_tol} crit={new_crit}")
        return {"avg": round(avg, 1), "new_tol": new_tol, "new_crit": new_crit}

    def set_volume(self, vol: float):
        with self._lock:
            self._state["volume"] = max(0.0, min(1.0, vol))

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._state)

    # ------------------------------------------------------------------
    # Logica cicli CHECK / COMFORT
    # ------------------------------------------------------------------
    def _start_logic_thread(self):
        if self._logic_thread and self._logic_thread.is_alive():
            return
        self._logic_thread = threading.Thread(
            target=self._logic_loop, daemon=True
        )
        self._logic_thread.start()

    def _logic_loop(self):
        while True:
            with self._lock:
                active = self._state["active"]
            if not active:
                time.sleep(0.5)
                continue

            # --- FASE CHECK ---
            with self._lock:
                self._state["mode"] = "CHECK"
                self._state["needs_comfort"] = False

            for i in range(self.check_duration, 0, -1):
                with self._lock:
                    if not self._state["active"]:
                        return
                    self._state["countdown"] = i
                time.sleep(1)

            # --- DECISIONE ---
            with self._lock:
                needs = self._state["needs_comfort"]
                active = self._state["active"]

            if active and needs:
                # --- FASE COMFORT ---
                with self._lock:
                    self._state["mode"] = "COMFORT"

                for i in range(self.comfort_duration, 0, -1):
                    with self._lock:
                        if not self._state["active"]:
                            return
                        self._state["countdown"] = i
                    time.sleep(1)

            # riparte da CHECK

    # ------------------------------------------------------------------
    # Chiamato da process() ad ogni frame audio
    # ------------------------------------------------------------------
    def process(self, data: Dict[str, Any]) -> Dict[str, Any]:
        db = data.get("audio_db", 0.0)
        with self._lock:
            th_crit = self._state["th_crit"]
            mode    = self._state["mode"]
            if mode == "CHECK" and db >= th_crit:
                self._state["needs_comfort"] = True
        return data