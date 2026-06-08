import influxdb_client
from influxdb_client.client.write_api import SYNCHRONOUS
from typing import Dict, Any
from ..config import config
from ..logger import logger

# Campi numerici inviati a InfluxDB (nomi storici mantenuti per non rompere le dashboard).
INFLUX_FIELDS = [
    "PM1-Particolato-[µg/m^3]",
    "PM2_5-Particolato-[µg/m^3]",
    "PM10-Particolato-[µg/m^3]",
    "CO2-AnidrideCarbonica-[ppm]",
    "TVOC-QualitaAria-[G]",
    "TEMP-[C]",
    "HUM-[%]",
    "CH2O-Formaldeie-[mg/m^3]",
    "CO-MonossidoDiCarbonio-[ppm]",
    "O3-Ozono-[ppm]",
    "NO2-BiossidoDiAzoto-[ppm]",
    "lux-IntensitaLuminosa",
    "IAQI",
]


class TelegrafClient:
    """Invia i dati direttamente a InfluxDB Cloud.

    Connessione e tag sono presi dalla configurazione; il token viene letto
    esclusivamente dall'ambiente (INFLUXDB_TOKEN) e mai dal codice o dallo YAML.
    """

    def __init__(self):
        tcfg = config.communicator.telegraf
        self.url = tcfg.get("url", "https://influxdb1.digisense.it")
        self.org = tcfg.get("org", "Digiplus")
        self.bucket = tcfg.get("bucket", "health_data")
        self.measurement = tcfg.get("measurement", "ZPHSensor_sensore")
        self.tags = tcfg.get("tags", {}) or {}

        self.token = config.secrets.influxdb_token
        self.client = None
        self.write_api = None

        if not self.token:
            logger.error(
                "INFLUXDB_TOKEN non impostato: invio a InfluxDB disabilitato. "
                "Imposta la variabile in .env."
            )
            return

        self.client = influxdb_client.InfluxDBClient(
            url=self.url, token=self.token, org=self.org
        )
        self.write_api = self.client.write_api(write_options=SYNCHRONOUS)

    def send(self, data: Dict[str, Any]):
        """Invia i dati mantenendo i nomi originali delle variabili."""
        if self.write_api is None:
            return
        try:
            point = influxdb_client.Point(self.measurement)

            for key in INFLUX_FIELDS:
                val = data.get(key)
                if val is not None:
                    point.field(key, float(val))

            for tag_key, tag_val in self.tags.items():
                point.tag(tag_key, tag_val)

            self.write_api.write(bucket=self.bucket, org=self.org, record=point)

        except Exception as e:
            logger.error(f"Errore durante l'invio a InfluxDB: {e}")
