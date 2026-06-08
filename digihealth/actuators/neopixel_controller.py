import time
import datetime
import math
import threading
from typing import Dict, Any, Optional
from ..logger import logger

# Durata del lampeggio quando arriva un alert (secondi).
ALERT_BLINK_DURATION_S = 5.0
# Periodo ON/OFF: 0.25s -> 2 Hz, ben visibile.
ALERT_BLINK_PERIOD_S = 0.25

class NeoPixelController:

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.num_pixels = config.get('num_pixels', 144)
        self.iaqi_range = config.get('iaqi_range', [0, 79])
        self.circadian_range = config.get('circadian_range', [80, 143])
        self.start_time = time.time()
        self.pixels = None
        self._last_color_hex = '#000000'
        self._last_iaqi = 0
        self._active = False
        self._alert_until: float = 0.0          # blocca update() durante il blink
        self._alert_color: tuple = (255, 0, 0)
        self._blink_thread: Optional[threading.Thread] = None
        self._blink_stop = threading.Event()

        try:
            import board
            import neopixel
            pin = getattr(board, f"D{config.get('pin', 12)}")
            self.pixels = neopixel.NeoPixel(
                pin, self.num_pixels,
                brightness=1.0, auto_write=False,
                pixel_order=neopixel.GRB
            )
            logger.info("NeoPixel inizializzato correttamente")
        except Exception as e:
            logger.error(f"NeoPixel non disponibile (permessi?): {e}")
            logger.warning("LED disabilitati — il resto del sistema continua normalmente")

    def set_alert(self, color: tuple, hold_seconds: float):
        """Avvia un lampeggio del colore di allarme per ALERT_BLINK_DURATION_S
        secondi; alla fine update() riprende il pattern IAQI/circadiano.
        Il parametro hold_seconds e' ignorato (mantenuto per compatibilita'
        di firma con gli altri attuatori)."""
        if self.pixels is None:
            return

        # Ferma un eventuale lampeggio precedente prima di partire col nuovo.
        if self._blink_thread and self._blink_thread.is_alive():
            self._blink_stop.set()
            self._blink_thread.join(timeout=0.5)
        self._blink_stop.clear()

        self._alert_color = color
        # Margine extra cosi' update() non interferisce nemmeno se il thread
        # tarda a pulire _alert_until per qualche millisecondo.
        self._alert_until = time.time() + ALERT_BLINK_DURATION_S + 0.5

        self._blink_thread = threading.Thread(
            target=self._blink_alert,
            args=(color, ALERT_BLINK_DURATION_S),
            daemon=True,
            name="NeoPixelBlink",
        )
        self._blink_thread.start()
        logger.info(f"NeoPixel: ALERT lampeggio {color} per {ALERT_BLINK_DURATION_S:.0f}s")

    def _blink_alert(self, color: tuple, duration: float):
        end = time.time() + duration
        on = True
        try:
            while time.time() < end and not self._blink_stop.is_set():
                try:
                    self.pixels.fill(color if on else (0, 0, 0))
                    self.pixels.show()
                    self._active = on
                    rgb = color if on else (0, 0, 0)
                    self._last_color_hex = '#{:02x}{:02x}{:02x}'.format(*rgb)
                except Exception as e:
                    logger.error(f"NeoPixel blink: {e}")
                    break
                on = not on
                time.sleep(ALERT_BLINK_PERIOD_S)
        finally:
            # Sblocca update() che ripristinera' il pattern normale.
            self._alert_until = 0.0

    def update(self, data: Dict[str, Any]):
        if self.pixels is None:
            return  # nessun crash, sistema continua

        # Durante il lampeggio i pixel sono gestiti dal thread di blink.
        if time.time() < self._alert_until:
            return

        try:
            iaqi = data.get('IAQI', 0)
            self._last_iaqi = iaqi

            if not self._is_active_time():
                self.pixels.fill((0, 0, 0))
                self.pixels.show()
                self._active = False
                self._last_color_hex = '#000000'
                return

            color = self._get_iaqi_color(iaqi)
            self._active = True
            self._last_color_hex = '#{:02x}{:02x}{:02x}'.format(*color)
            self._set_iaqi_breathing(color)

            # -- Segmento circadiano disabilitato: la luce circadiana è gestita
            #    dalle lampadine Shelly. Decommentare per riattivare.
            # lux = data.get('lux-IntensitaLuminosa', 0)
            # temp_k, brightness = self._calculate_circadian_light(lux)
            # rgb = self._kelvin_to_rgb(temp_k)
            # self._set_circadian_segment(rgb, brightness)

            self.pixels.show()

        except Exception as e:
            logger.error(f"Error updating NeoPixel: {e}")

    def _is_active_time(self) -> bool:
        now = datetime.datetime.now()
        current_minutes = now.hour * 60 + now.minute
        start_minutes = 8 * 60 + 10   # 08:10
        end_minutes   = 18 * 60 + 40  # 18:40
        return start_minutes <= current_minutes < end_minutes

    def _get_iaqi_color(self, iaqi: int) -> tuple:
        if iaqi <= 25:  return (0, 180, 255)
        elif iaqi <= 50:  return (0, 255, 0)
        elif iaqi <= 100: return (255, 255, 0)
        elif iaqi <= 150: return (255, 140, 0)
        elif iaqi <= 170: return (255, 165, 0)
        else:             return (255, 0, 0)

    def _set_iaqi_breathing(self, color: tuple):
        r, g, b = color
        factor = 0.2 + 0.8 * (math.sin((time.time() - self.start_time) * 0.05) + 1) / 2
        limiter = 0.3
        for i in range(self.num_pixels):
            self.pixels[i] = (int(r*factor*limiter), int(g*factor*limiter), int(b*factor*limiter))

    def _kelvin_to_rgb(self, temp_k: int) -> tuple:
        return (255, 255, 255) if temp_k >= 5000 else (255, 180, 100)

    def _calculate_circadian_light(self, lux: float) -> tuple:
        temp_k = 6500 if 7 <= datetime.datetime.now().hour < 16 else 2700
        return temp_k, 10

    def get_status(self) -> dict:
        return {
            'available': self.pixels is not None,
            'active': self._active,
            'color_hex': self._last_color_hex,
            'iaqi': self._last_iaqi,
        }

    def _set_circadian_segment(self, rgb: tuple, brightness: float):
        r, g, b = rgb
        factor = brightness / 100
        for i in range(self.circadian_range[0], self.circadian_range[1] + 1):
            self.pixels[i] = (int(r*factor), int(g*factor), int(b*factor))
