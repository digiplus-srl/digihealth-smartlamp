from typing import Dict, Any
from ..config import config
from ..logger import logger

class CommunicatorManager:
    """Manages data communication."""

    def __init__(self):
        self.communicators = []
        self._load_communicators()

    def _load_communicators(self):
        """Load available communicators."""
        if config.communicator.telegraf.get('enabled', True):
            from .telegraf_client import TelegrafClient
            self.communicators.append(TelegrafClient())

    def send(self, data: Dict[str, Any]):
        """Send data through all communicators."""
        for comm in self.communicators:
            try:
                comm.send(data)
            except Exception as e:
                logger.error(f"Error sending data: {e}")