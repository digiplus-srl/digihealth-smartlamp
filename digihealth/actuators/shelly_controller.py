import datetime
import requests
from typing import Dict, Any, List
from ..logger import logger

# Lux range for adaptive brightness:
# >= LUX_MAX → minimum brightness (10%), <= LUX_MIN → full brightness (100%)
LUX_MIN = 0
LUX_MAX = 90


class ShellyController:
    """Controls one or more Shelly smart bulbs via HTTP API.

    Each cycle: calculates circadian color temperature and adaptive brightness
    from ambient lux, then pushes the command to every online device.
    """

    def __init__(self, config: Dict[str, Any]):
        self.devices: List[Dict[str, Any]] = [
            d for d in config.get('devices', [])
            if d.get('enabled', True)
        ]
        self._states: dict = {
            d.get('ip', ''): {'name': d.get('name', d.get('ip', '?')), 'online': False, 'brightness': 0, 'temp_k': 2700}
            for d in self.devices
        }
        if not self.devices:
            logger.warning("ShellyController: nessun dispositivo abilitato in config")
        else:
            names = [d.get('name', d.get('ip', '?')) for d in self.devices]
            logger.info(f"ShellyController: dispositivi caricati → {names}")

    def update(self, data: Dict[str, Any]):
        """Aggiorna tutte le Shelly abilitate in base ai dati sensori."""
        lux = data.get('lux-IntensitaLuminosa', 0)
        hour = datetime.datetime.now().hour
        brightness = self._calc_brightness(lux)
        temp_k = self._calc_color_temp(hour)

        for device in self.devices:
            ip = device.get('ip')
            name = device.get('name', ip)
            try:
                online = self._is_online(ip)
                self._states[ip]['online'] = online
                if not online:
                    logger.debug(f"Shelly '{name}' ({ip}) offline, salto")
                    continue
                self._send_command(ip, brightness, temp_k)
                self._states[ip]['brightness'] = brightness
                self._states[ip]['temp_k'] = temp_k
                logger.debug(f"Shelly '{name}': brightness={brightness}% temp={temp_k}K lux={lux}")
            except requests.Timeout:
                logger.warning(f"Shelly '{name}' ({ip}): timeout")
            except requests.ConnectionError:
                logger.warning(f"Shelly '{name}' ({ip}): non raggiungibile")
            except Exception as e:
                logger.warning(f"Shelly '{name}' ({ip}): errore {e}")

    def get_status(self) -> dict:
        return {'devices': list(self._states.values())}

    def _is_online(self, ip: str) -> bool:
        try:
            requests.get(f"http://{ip}/status", timeout=1)
            return True
        except requests.RequestException:
            return False

    def _send_command(self, ip: str, brightness: int, temp_k: int):
        url = f"http://{ip}/light/0?turn=on&brightness={brightness}&temp={temp_k}"
        requests.get(url, timeout=2)

    def _calc_brightness(self, lux: float) -> int:
        """Dimming adattivo: lux alto → poca luce, lux basso → massima luce."""
        if lux >= LUX_MAX:
            return 10
        if lux <= LUX_MIN:
            return 100
        return int(round(100 - ((lux - LUX_MIN) / (LUX_MAX - LUX_MIN)) * 90))

    def _calc_color_temp(self, hour: int) -> int:
        """Luce circadiana: fredda di giorno (7-15), calda la sera."""
        return 6500 if 7 <= hour < 16 else 2700
