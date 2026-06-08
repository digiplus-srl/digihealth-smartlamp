from typing import Dict, Any
from ..config import config
from ..logger import logger

class ProcessorManager:
    """Manages data processors."""

    def __init__(self):
        self.processors = []
        self._load_processors()

    def _load_processors(self):
        """Load available processors."""
        if config.processors.iaqi.get('enabled', True):
            from .iaqi import IAQIProcessor
            self.processors.append(IAQIProcessor())

    def process(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Process sensor data through all processors."""
        processed = data.copy()
        for processor in self.processors:
            processed = processor.process(processed)
        return processed