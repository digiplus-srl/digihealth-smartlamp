import logging
from .config import config

def setup_logging():
    """Setup structured logging."""
    logging.basicConfig(
        level=getattr(logging, config.logging.level.upper()),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(config.logging.file) if config.logging.file else logging.NullHandler()
        ]
    )

logger = logging.getLogger(__name__)