import yaml
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import os
from dotenv import load_dotenv

# Carica le variabili da .env (se presente) prima di leggere i secret.
load_dotenv()

class SensorConfig(BaseModel):
    zph: Dict[str, Any] = Field(default_factory=dict)
    light: Dict[str, Any] = Field(default_factory=dict)
    door: Dict[str, Any] = Field(default_factory=dict)
    window: Dict[str, Any] = Field(default_factory=dict)
    microphone: Dict[str, Any] = Field(default_factory=dict)

class ProcessorConfig(BaseModel):
    iaqi: Dict[str, Any] = Field(default_factory=dict)
    circadian: Dict[str, Any] = Field(default_factory=dict)
    audio_comfort: Dict[str, Any] = Field(default_factory=dict)

class ActuatorConfig(BaseModel):
    neopixel: Dict[str, Any] = Field(default_factory=dict)
    shelly: Dict[str, Any] = Field(default_factory=dict)
    tuya_purifier: Dict[str, Any] = Field(default_factory=dict)
    tuya_ac: Dict[str, Any] = Field(default_factory=dict)

class CommunicatorConfig(BaseModel):
    telegraf: Dict[str, Any] = Field(default_factory=dict)
    ipc: Dict[str, Any] = Field(default_factory=dict)

class WebConfig(BaseModel):
    enabled: bool = False
    host: str = "0.0.0.0"
    port: int = 5000

class TelegramConfig(BaseModel):
    enabled: bool = False

class LoggingConfig(BaseModel):
    level: str = "INFO"
    file: Optional[str] = None

class Secrets(BaseModel):
    """Segreti caricati da variabili d'ambiente (mai da YAML/git)."""
    influxdb_token: Optional[str] = Field(default_factory=lambda: os.getenv("INFLUXDB_TOKEN"))
    api_key: Optional[str] = Field(default_factory=lambda: os.getenv("DIGIHEALTH_API_KEY"))
    telegram_bot_token: Optional[str] = Field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: Optional[str] = Field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID"))

class DigiHealthConfig(BaseModel):
    sensors: SensorConfig = Field(default_factory=SensorConfig)
    processors: ProcessorConfig = Field(default_factory=ProcessorConfig)
    actuators: ActuatorConfig = Field(default_factory=ActuatorConfig)
    communicator: CommunicatorConfig = Field(default_factory=CommunicatorConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    thresholds: Dict[str, Any] = Field(default_factory=dict)
    secrets: Secrets = Field(default_factory=Secrets)

def load_config(config_path: str = "config/default.yaml") -> DigiHealthConfig:
    """Load configuration from YAML file."""
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            data = yaml.safe_load(f)
        return DigiHealthConfig(**data)
    else:
        return DigiHealthConfig()

import platform as _platform
_default_cfg = (
    "config/windows.yaml"
    if _platform.system() == "Windows"
    else "config/default.yaml"
)
config = load_config(os.environ.get('DIGIHEALTH_CONFIG', _default_cfg))