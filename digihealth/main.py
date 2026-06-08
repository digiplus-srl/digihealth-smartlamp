#!/usr/bin/env python3
"""
DigiHealth Lamp - Main Entry Point
Smart lamp with environmental monitoring and InfluxDB integration.
"""

import sys
import time
import threading
from .config import config
from .logger import setup_logging, logger

# Core modules
from .sensors import SensorManager
from .processors import ProcessorManager
from .communicator import CommunicatorManager

# Optional modules
try:
    from .actuators import ActuatorManager
    actuators_available = True
except ImportError:
    actuators_available = False
    logger.warning("Actuators module not available")

try:
    from .web import WebManager
    web_available = True
except ImportError:
    web_available = False
    logger.warning("Web module not available")

def main():
    """Main application entry point."""
    setup_logging()
    logger.info("Starting DigiHealth Lamp")

    # Initialize core managers
    sensor_manager = SensorManager()
    processor_manager = ProcessorManager()
    communicator_manager = CommunicatorManager()

    # Initialize optional managers
    actuator_manager = ActuatorManager() if actuators_available else None
    web_manager = WebManager() if web_available and config.web.enabled else None

    # Popola subito lo stato attuatori nella dashboard (senza aspettare il ciclo sensori)
    if actuator_manager and web_manager:
        # Collega gli attuatori al dispatcher: gli alert in arrivo all'API li azionano
        web_manager.set_actuator_manager(actuator_manager)
        web_manager.update_actuators(actuator_manager.get_status())

    # Start threads
    threads = []

    # Sensor collection thread
    def sensor_loop():
        while True:
            # 1. Raccolta dati grezzi dai sensori
            data = sensor_manager.collect_all()
            
            # 2. Elaborazione (calcolo IAQI, medie, ecc.)
            processed_data = processor_manager.process(data)
            
            # 3. Invio a Telegraf/InfluxDB
            communicator_manager.send(processed_data)

            # --- AGGIUNTA PER LA DASHBOARD WEB ---
            if web_manager:
                # Questo metodo deve essere implementato in digihealth/web/__init__.py
                web_manager.update_data(processed_data)
            # -------------------------------------

            # 4. Controllo LED (se presenti)
            if actuator_manager:
                actuator_manager.update(processed_data)
                if web_manager:
                    web_manager.update_actuators(actuator_manager.get_status())

            time.sleep(30)  # Ciclo ogni 30 secondi

    threads.append(threading.Thread(target=sensor_loop, daemon=True))

    # Web server thread (if enabled)
    if web_manager:
        threads.append(threading.Thread(target=web_manager.run, daemon=True))

    # Start all threads
    for thread in threads:
        thread.start()

    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down DigiHealth Lamp")
        sys.exit(0)

if __name__ == "__main__":
    main()
