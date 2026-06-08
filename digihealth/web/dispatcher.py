"""Dispatch delle azioni locali a partire dagli alert ricevuti.

Mappa ogni alert (per `dominant_pollutant` + `level`) in azioni sugli attuatori
reali gestiti dall'ActuatorManager (stesso processo del web).

Conflitto col controllo autonomo: ogni azione "forza" il dispositivo per una
durata (`action_due_minutes` del payload, fallback DEFAULT_HOLD_MIN minuti).
Durante la forzatura il ciclo sensori a 30s non tocca il dispositivo
(vedi guardie `_override_until` / `_alert_until` negli attuatori).
"""
import threading
import time
from typing import List, Optional
from ..logger import logger
from . import storage
from .schemas import NormalizedAlert
from ..notifications import telegram_notifier

DEFAULT_HOLD_MIN = 15.0           # durata forzatura se l'alert non la specifica
COLOR_CRITICAL = (255, 0, 0)      # rosso
COLOR_WARNING = (255, 140, 0)     # arancione

# Token per classificare l'inquinante dominante / la metrica scatenante.
AIR_TOKENS = ("CO2", "TVOC", "PM", "CH2O", "FORMALDE", "O3", "NO2", "MONOSSIDO", "CO-")
TEMP_TOKENS = ("TEMP", "TEMPERATURA")


class ActionDispatcher:
    """Traduce un alert in comandi sugli attuatori abilitati."""

    def __init__(self):
        self.actuator_manager = None  # iniettato da WebManager.set_actuator_manager

    def bind(self, actuator_manager):
        self.actuator_manager = actuator_manager
        devices = list(getattr(actuator_manager, "_actuator_map", {}).keys())
        logger.info(f"ActionDispatcher: collegato all'ActuatorManager (dispositivi: {devices})")

    def _device(self, name: str):
        """Istanza viva dell'attuatore, o None se non caricato/abilitato."""
        if self.actuator_manager is None:
            return None
        return getattr(self.actuator_manager, "_actuator_map", {}).get(name)

    @staticmethod
    def _hold_seconds(alert: NormalizedAlert) -> float:
        mins = alert.action_due_minutes if alert.action_due_minutes else DEFAULT_HOLD_MIN
        return float(mins) * 60.0

    @staticmethod
    def _classify(alert: NormalizedAlert) -> str:
        """'air' | 'temp' | '' in base a dominant_pollutant e metrica scatenante."""
        blob = f"{alert.dominant_pollutant or ''} {alert.trigger_metric or ''}".upper()
        if any(tok in blob for tok in TEMP_TOKENS):
            return "temp"
        if any(tok in blob for tok in AIR_TOKENS):
            return "air"
        return ""

    def dispatch(self, alert: NormalizedAlert, alert_id: int,
                 client_ip: Optional[str] = None) -> dict:
        """Esegue le azioni per l'alert e registra l'esito su SQLite."""
        hold = self._hold_seconds(alert)
        level = (alert.level or "").upper()
        category = self._classify(alert)
        done: List[str] = []

        # 1) Azione "fisica" in base all'inquinante dominante
        if category == "air":
            self._fire("tuya_purifier", "force_on", hold, done, "purificatore ON")
        elif category == "temp":
            self._fire("tuya_ac", "force_on", hold, done, "AC ON")

        # 2) Segnale visivo in base alla gravità
        if level == "CRITICAL":
            self._fire_alert_color(COLOR_CRITICAL, hold, done, "NeoPixel rosso")
        elif level in ("WARNING", "WARN"):
            self._fire_alert_color(COLOR_WARNING, hold, done, "NeoPixel arancione")

        # 3) Shelly: solo notifica in dashboard, niente azione fisica
        #    (lampade smart sotto controllo autonomo circadiano)
        done.append("Shelly notificata")

        summary = "; ".join(done) if done else "nessuna azione (dispositivo non disponibile o alert non mappato)"
        logger.info(
            "Alert id=%s code=%s level=%s dominant=%s -> %s (hold=%.0fs) (da %s)",
            alert_id, alert.action_code, alert.level, alert.dominant_pollutant,
            summary, hold, client_ip or "?",
        )
        storage.mark_processed(alert_id, summary)

        # Pubblica l'evento per la dashboard (toast notification).
        try:
            from . import set_alert_event
            set_alert_event(alert_id, alert.level, alert.dominant_pollutant,
                            alert.action_code, done, hold)
        except Exception as e:
            logger.debug(f"Dispatcher: set_alert_event fallito: {e}")

        # Notifica Telegram (non bloccante).
        try:
            from ..config import config
            if config.telegram.enabled:
                telegram_notifier.send_alert(alert, alert_id, summary, category)
        except Exception as e:
            logger.debug(f"Dispatcher: telegram_notifier fallito: {e}")

        return {"action_taken": summary, "targets": done, "hold_seconds": hold}

    def _fire(self, device_name: str, method: str, hold: float,
              done: List[str], label: str):
        dev = self._device(device_name)
        if dev is None or not hasattr(dev, method):
            return
        try:
            getattr(dev, method)(hold)
            done.append(label)
        except Exception as e:
            logger.warning(f"Dispatcher: errore su {device_name}.{method}: {e}")

    def _fire_alert_color(self, color: tuple, hold: float, done: List[str], label: str):
        dev = self._device("neopixel")
        if dev is None or not hasattr(dev, "set_alert"):
            return
        try:
            dev.set_alert(color, hold)
            done.append(label)
        except Exception as e:
            logger.warning(f"Dispatcher: errore su neopixel.set_alert: {e}")
