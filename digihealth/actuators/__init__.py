from typing import Dict, Any
from ..config import config
from ..logger import logger

class ActuatorManager:
    """Manages all actuators."""

    def __init__(self):
        self.actuators = []
        self._actuator_map: Dict[str, Any] = {}
        self._load_actuators()

    def _load_actuators(self):
        """Load available actuators."""
        if config.actuators.neopixel.get('enabled', True):
            try:
                from .neopixel_controller import NeoPixelController
                a = NeoPixelController(config.actuators.neopixel)
                self.actuators.append(a)
                self._actuator_map['neopixel'] = a
                logger.info("NeoPixel actuator loaded")
            except Exception as e:
                logger.warning(f"NeoPixel actuator not available: {e}")

        if config.actuators.shelly.get('enabled', False):
            try:
                from .shelly_controller import ShellyController
                a = ShellyController(config.actuators.shelly)
                self.actuators.append(a)
                self._actuator_map['shelly'] = a
                logger.info("Shelly actuator loaded")
            except Exception as e:
                logger.warning(f"Shelly actuator not available: {e}")

        thr = config.thresholds or {}

        if config.actuators.tuya_purifier.get('enabled', False):
            try:
                from .tuya_purifier import TuyaPurifier
                pur_cfg = dict(config.actuators.tuya_purifier)
                if thr.get('pm25', {}).get('tolerance') is not None:
                    pur_cfg['pm25_limit'] = thr['pm25']['tolerance']
                if thr.get('co2', {}).get('tolerance') is not None:
                    pur_cfg['co2_limit'] = thr['co2']['tolerance']
                a = TuyaPurifier(pur_cfg)
                self.actuators.append(a)
                self._actuator_map['tuya_purifier'] = a
                logger.info("Tuya purifier actuator loaded")
            except Exception as e:
                logger.warning(f"Tuya purifier actuator not available: {e}")

        if config.actuators.tuya_ac.get('enabled', False):
            try:
                from .tuya_ac import TuyaAC
                ac_cfg = dict(config.actuators.tuya_ac)
                a = TuyaAC(ac_cfg)
                self.actuators.append(a)
                self._actuator_map['tuya_ac'] = a
                logger.info("Tuya AC actuator loaded")
            except Exception as e:
                logger.warning(f"Tuya AC actuator not available: {e}")

    def update(self, data: Dict[str, Any]):
        """Update actuators based on sensor data."""
        for actuator in self.actuators:
            actuator.update(data)

    def get_status(self) -> Dict[str, Any]:
        return {key: a.get_status() for key, a in self._actuator_map.items()}