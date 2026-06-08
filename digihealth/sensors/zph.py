import serial
from typing import Dict, Any, Optional
from .base import BaseSensor
import time

class ZPHSensor(BaseSensor):
    """ZPH01B Air Quality Sensor via Serial."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.port = config.get('port', '/dev/serial0')
        self.baudrate = config.get('baudrate', 9600)
        self.command = bytes(config.get('command', [255, 1, 134, 0, 0, 0, 0, 0, 121]))
        self.ser: Optional[serial.Serial] = None

    def is_available(self) -> bool:
        try:
            self.ser = serial.Serial(port=self.port, baudrate=self.baudrate, timeout=2)
            self.ser.close()
            return True
        except Exception as e:
            self.logger.error(f"ZPH sensor not available: {e}")
            return False

    def read(self) -> Dict[str, Any]:
        if not self.ser:
            if not self.is_available():
                return {}
        try:
            self.ser.open()
            self.ser.write(self.command)
            time.sleep(0.1)
            response = self.ser.read(26)
            self.ser.close()
            return self._parse_data(response)
        except Exception as e:
            self.logger.error(f"Error reading ZPH sensor: {e}")
            return {}

    def _parse_data(self, data: bytes) -> Dict[str, Any]:
        if len(data) != 26:
            self.logger.warning(f"Invalid ZPH data length: {len(data)}")
            return {}

        return {
            "PM1-Particolato-[µg/m^3]": data[2] * 256 + data[3],
            "PM2_5-Particolato-[µg/m^3]": data[4] * 256 + data[5],
            "PM10-Particolato-[µg/m^3]": data[6] * 256 + data[7],
            "CO2-AnidrideCarbonica-[ppm]": data[8] * 256 + data[9],
            "TVOC-QualitaAria-[G]": data[10],
            "TEMP-[C]": ((data[11] * 256 + data[12]) - 500) * 0.1,
            "HUM-[%]": data[13] * 256 + data[14],
            "CH2O-Formaldeie-[mg/m^3]": (data[15] * 256 + data[16]) * 0.001,
            "CO-MonossidoDiCarbonio-[ppm]": (data[17] * 256 + data[18]) * 0.1,
            "O3-Ozono-[ppm]": (data[19] * 256 + data[20]) * 0.01,
            "NO2-BiossidoDiAzoto-[ppm]": (data[21] * 256 + data[22]) * 0.01
        }
