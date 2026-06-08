import smbus2 as smbus
import time
from typing import Dict, Any
from .base import BaseSensor

class LightSensor(BaseSensor):
    """BH1750 Light Sensor via I2C."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.bus_number = config.get('i2c_bus', 1)
        self.address = config.get('address', 0x23)
        self.bus = smbus.SMBus(self.bus_number)

    def is_available(self) -> bool:
        try:
            # Test read
            self.bus.read_byte(self.address)
            return True
        except Exception as e:
            self.logger.error(f"Light sensor not available: {e}")
            return False

    def read(self) -> Dict[str, Any]:
        try:
            # Power on
            self.bus.write_byte(self.address, 0x01)
            # High res mode
            self.bus.write_byte(self.address, 0x20)
            # Wait for conversion
            time.sleep(0.2)
            # Read data
            data = self.bus.read_i2c_block_data(self.address, 0x00, 2)
            lux = (data[0] << 8 | data[1]) / 1.2
            return {"lux-IntensitaLuminosa": round(lux, 2)}
        except Exception as e:
            self.logger.error(f"Error reading light sensor: {e}")
            return {}