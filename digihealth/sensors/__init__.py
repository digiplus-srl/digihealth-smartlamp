from typing import Dict, Any, List
from .base import BaseSensor
from ..config import config
from ..logger import logger

try:
    from .zph import ZPHSensor
except ImportError:
    ZPHSensor = None

try:
    from .light import LightSensor
except ImportError:
    LightSensor = None

class SensorManager:
    """Manages all sensors."""

    def __init__(self):
        self.sensors: Dict[str, BaseSensor] = {}
        self._load_sensors()

    def _load_sensors(self):
        """Load available sensors based on config."""
        sensor_classes = {}
        if ZPHSensor:   sensor_classes['zph']   = ZPHSensor
        if LightSensor: sensor_classes['light'] = LightSensor

        for sensor_name, sensor_class in sensor_classes.items():
            sensor_config = getattr(config.sensors, sensor_name, {})
            if not sensor_config.get('enabled', True):
                continue
            try:
                sensor = sensor_class(sensor_config)
                if sensor.is_available():
                    self.sensors[sensor_name] = sensor
                    logger.info(f"Loaded sensor: {sensor_name}")
                else:
                    logger.warning(f"Sensor not available: {sensor_name}")
            except Exception as e:
                logger.warning(f"Sensor {sensor_name} init failed: {e}")

    def collect_all(self) -> Dict[str, Any]:
        """Collect data from all sensors."""
        data = {}
        for name, sensor in self.sensors.items():
            sensor_data = sensor.read()
            data.update(sensor_data)
        return data