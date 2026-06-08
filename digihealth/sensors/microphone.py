import numpy as np
import threading
from collections import deque
from typing import Dict, Any, Optional
from .base import BaseSensor

try:
    import pyaudio
    PYAUDIO_AVAILABLE = True
except ImportError:
    PYAUDIO_AVAILABLE = False

NUM_BARS = 48        # barre equalizzatore
CHUNK    = 1024      # campioni per frame (~46ms a 44100Hz)
RATE     = 16000
DB_HISTORY = 8       # media mobile anti-spike (~370ms)

# Bande di frequenza in Hz per le 48 barre (scala logaritmica percettiva)
def _log_bins(n, rate, chunk):
    fmin, fmax = 40.0, 7000.0
    edges = np.logspace(np.log10(fmin), np.log10(fmax), n + 1)
    freqs = np.fft.rfftfreq(chunk, d=1.0 / rate)
    bins = []
    for i in range(n):
        lo = np.searchsorted(freqs, edges[i])
        hi = np.searchsorted(freqs, edges[i + 1])
        hi = max(hi, lo + 1)
        bins.append((lo, hi))
    return bins


class MicrophoneSensor(BaseSensor):
    """Microfono USB – lettura continua in background, esposizione thread-safe."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.device_index: Optional[int] = config.get('device_index', None)
        self._pa: Optional[Any] = None
        self._stream: Optional[Any] = None
        self._lock = threading.Lock()
        self._db_history: deque = deque(maxlen=DB_HISTORY)
        self._latest: Dict[str, Any] = {
            "audio_db": 0.0,
            "audio_spectrum": [0] * NUM_BARS,
        }
        self._bins = _log_bins(NUM_BARS, RATE, CHUNK)
        self._running = False
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    def is_available(self) -> bool:
        if not PYAUDIO_AVAILABLE:
            self.logger.error("pyaudio non installato")
            return False
        try:
            pa = pyaudio.PyAudio()
            if self.device_index is not None:
                pa.get_device_info_by_index(self.device_index)
            else:
                # cerca il primo dispositivo con canali di input
                found = False
                for i in range(pa.get_device_count()):
                    info = pa.get_device_info_by_index(i)
                    if info.get('maxInputChannels', 0) > 0:
                        found = True
                        break
                if not found:
                    pa.terminate()
                    return False
            pa.terminate()
            return True
        except Exception as e:
            self.logger.error(f"Microfono non disponibile: {e}")
            return False

    # ------------------------------------------------------------------
    def start(self):
        """Avvia il thread di lettura continua."""
        if self._running:
            return
        if not PYAUDIO_AVAILABLE:
            return
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        self.logger.info("MicrophoneSensor: thread avviato")

    def stop(self):
        self._running = False
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
        if self._pa:
            try:
                self._pa.terminate()
            except Exception:
                pass

    # ------------------------------------------------------------------
    def _capture_loop(self):
        self._pa = pyaudio.PyAudio()
        open_kwargs = dict(
            format=pyaudio.paInt16,
            channels=1,
            rate=RATE,
            input=True,
            frames_per_buffer=CHUNK,
        )
        if self.device_index is not None:
            open_kwargs['input_device_index'] = self.device_index

        try:
            self._stream = self._pa.open(**open_kwargs)
        except Exception as e:
            self.logger.error(f"Impossibile aprire stream microfono: {e}")
            self._running = False
            return

        while self._running:
            try:
                raw = self._stream.read(CHUNK, exception_on_overflow=False)
                data = np.frombuffer(raw, dtype=np.int16).astype(np.float32)

                # --- dB SPL relativo ---
                rms = np.sqrt(np.mean(data ** 2))
                db = float(20 * np.log10(rms / 32768.0) + 95) if rms > 0 else 0.0
                db = max(0.0, db)

                # --- media mobile anti-spike ---
                self._db_history.append(db)
                smooth_db = float(np.mean(self._db_history))

                # --- FFT su scala logaritmica ---
                fft_mag = np.abs(np.fft.rfft(data * np.hanning(len(data))))
                spectrum = []
                for lo, hi in self._bins:
                    lo = min(lo, len(fft_mag) - 1)
                    hi = min(hi, len(fft_mag))
                    hi = max(hi, lo + 1)
                    val = float(np.mean(fft_mag[lo:hi]))
                    val = 0.0 if (np.isnan(val) or np.isinf(val)) else val
                    bar = min(100, int(val / 400))
                    spectrum.append(bar)

                with self._lock:
                    self._latest = {
                        "audio_db": round(smooth_db, 1),
                        "audio_spectrum": spectrum,
                    }
            except Exception as e:
                self.logger.warning(f"Errore lettura microfono: {e}")
                import time; import time as t; t.sleep(0.05)

    # ------------------------------------------------------------------
    def read(self) -> Dict[str, Any]:
        """Restituisce l'ultimo frame disponibile (thread-safe)."""
        with self._lock:
            return dict(self._latest)
