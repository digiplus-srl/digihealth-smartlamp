from abc import ABC, abstractmethod
from typing import Dict, Any
from ..logger import logger

class BaseSensor(ABC):
    """Base class for all sensors."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logger

    @abstractmethod
    def read(self) -> Dict[str, Any]:
        """Read sensor data. Returns dict with sensor readings."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if sensor is available."""
        pass