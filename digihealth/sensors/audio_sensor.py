"""
AudioSensor — legacy, non usato.
La gestione audio è centralizzata in digihealth/web/__init__.py (WebManager).
"""
import numpy as np
from ..logger import logger


class AudioSensor:
    """Sensore microfono via PyAudio. Non caricato da SensorManager."""

    def __init__(self):
        import pyaudio
        self.p     = pyaudio.PyAudio()
        self.CHUNK = 1024
        self.RATE  = 16000
        self.stream = None
        try:
            self.stream = self.p.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=self.RATE,
                input=True,
                output=False,
                frames_per_buffer=self.CHUNK,
            )
            logger.info("AudioSensor: stream aperto (input only)")
        except Exception as e:
            logger.error(f"AudioSensor: errore apertura stream: {e}")

    def collect(self):
        if not self.stream:
            return {"audio_level": 0, "spectrum": [0] * 20}
        try:
            raw  = self.stream.read(self.CHUNK, exception_on_overflow=False)
            data = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
            rms  = np.sqrt(np.mean(data ** 2))
            db   = 20 * np.log10(rms / 32768.0) + 95 if rms > 0 else 0.0
            fft  = np.abs(np.fft.fft(data))[:self.CHUNK // 2]
            bars = 20
            spectrum = [int(np.mean(fft[i * len(fft) // bars:(i + 1) * len(fft) // bars]) / 500)
                        for i in range(bars)]
            return {"audio_level": round(max(0.0, db), 1), "spectrum": spectrum}
        except Exception as e:
            logger.error(f"AudioSensor collect: {e}")
            return {"audio_level": 0, "spectrum": [0] * 20}

    def stop_all(self):
        try:
            if self.stream:
                self.stream.stop_stream()
                self.stream.close()
            self.p.terminate()
            logger.info("AudioSensor: risorse rilasciate")
        except Exception as e:
            logger.error(f"AudioSensor stop: {e}")
