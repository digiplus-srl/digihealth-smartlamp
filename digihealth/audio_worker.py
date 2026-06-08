"""
Audio Worker — processo separato (spawn).
Nessuna dipendenza da Flask o moduli web.
Tutto il codice PyAudio viene inizializzato qui dentro, mai nel processo principale.

Comandi in entrata (cmd_q):
  "start" | "stop" | "calibrate"
  ("volume", float) | ("comfort_mode", str) | ("audio_file", str)

Pacchetti in uscita (data_q) ogni ~100ms:
  {"db", "spectrum", "mode", "countdown", "active",
   "th_tol", "th_crit", "noise_detected", "fft_active",
   "comfort_mode", "cal_done"}
"""
import os, sys, time, threading, subprocess, wave, re, signal, tempfile
import queue as _q
from datetime import datetime
import numpy as np

# ── Costanti audio ────────────────────────────────────────────────────────────
RATE       = 44100
CHUNK      = 1024
NUM_BARS   = 48
PINK_WAV   = os.path.join(tempfile.gettempdir(), 'pink.wav')
IS_WINDOWS = sys.platform == 'win32'


def _log_bins(n, rate, chunk):
    fmin, fmax = 40.0, rate / 2.0 * 0.9
    edges = np.logspace(np.log10(fmin), np.log10(fmax), n + 1)
    freqs = np.fft.rfftfreq(chunk, d=1.0 / rate)
    bins  = []
    for i in range(n):
        lo = int(np.searchsorted(freqs, edges[i]))
        hi = int(np.searchsorted(freqs, edges[i + 1]))
        hi = max(hi, lo + 1)
        bins.append((min(lo, len(freqs) - 1), min(hi, len(freqs))))
    return bins


_BINS = _log_bins(NUM_BARS, RATE, CHUNK)

# ── Globals del sottoprocesso (non condivisi col processo principale) ─────────
_file_proc    = None
_file_lock    = threading.Lock()
_output_alsa  = None
_winsound_on  = False   # True quando winsound sta suonando (solo Windows WAV)


# ── Pink Noise 1/f via NumPy ──────────────────────────────────────────────────
def _generate_pink_noise_wav():
    path = PINK_WAV
    # Rigenera sempre (ignora cache) per assicurare qualità aggiornata
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else '.', exist_ok=True)

    # 60 secondi a 44100 Hz — loop quasi impercettibile
    duration_s = 60
    n = RATE * duration_s

    # Pink noise via filtro Voss-McCartney approssimato su FFT
    white = np.random.randn(n)
    fft_w = np.fft.rfft(white)
    freqs = np.fft.rfftfreq(n)
    freqs[0] = 1e-6
    pink = np.fft.irfft(fft_w / np.sqrt(np.abs(freqs)), n=n)

    # Normalizza a ±1, poi scala a 60% di dinamica — abbastanza udibile
    pink = pink / (np.max(np.abs(pink)) + 1e-9) * 0.60

    # Crossfade 2 s sui bordi → elimina il click al loop
    fade_len = RATE * 2
    fade_in  = np.linspace(0.0, 1.0, fade_len)
    fade_out = np.linspace(1.0, 0.0, fade_len)
    pink[:fade_len]  *= fade_in
    pink[-fade_len:] *= fade_out

    pcm = (pink * 32767).astype(np.int16)
    with wave.open(path, 'w') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(RATE)
        wf.writeframes(pcm.tobytes())
    return path


# ── Helpers riproduzione audio ────────────────────────────────────────────────
def _aplay_loop_cmd(filepath):
    parts = ["aplay", "-q"]
    if _output_alsa:
        parts += ["-D", _output_alsa]
    parts.append(f"'{filepath}'")
    return ["bash", "-c", f"while true; do {' '.join(parts)}; done"]


def _mpg123_cmd(filepath, volume=0.5):
    vol = int(32768 * volume)
    cmd = ["mpg123", "-q", "--loop", "-1", "-f", str(vol)]
    if _output_alsa:
        cmd += ["-o", "alsa", "-a", _output_alsa]
    cmd.append(filepath)
    return cmd


def _start_wav_windows(filepath):
    """WAV su Windows: winsound asincrono in loop — nessun subprocess, muore col processo."""
    global _winsound_on
    import winsound
    winsound.PlaySound(
        filepath,
        winsound.SND_FILENAME | winsound.SND_LOOP | winsound.SND_ASYNC
    )
    _winsound_on = True


def _stop_wav_windows():
    global _winsound_on
    if _winsound_on:
        try:
            import winsound
            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass
        _winsound_on = False


def _win_loop_mp3_cmds(filepath, volume=0.5):
    vol = int(32768 * volume)
    abs_path = os.path.abspath(filepath).replace("\\", "/")
    ps_media = (
        "Add-Type -AssemblyName PresentationCore; "
        "$mp = [Windows.Media.MediaPlayer]::new(); "
        f"$mp.Open([Uri]::new('{abs_path}')); "
        "$mp.Play(); "
        "while ($true) { "
        "  Start-Sleep -Milliseconds 200; "
        "  if ($mp.NaturalDuration.HasTimeSpan -and "
        "      $mp.Position -ge $mp.NaturalDuration.TimeSpan) { "
        "    $mp.Position = [TimeSpan]::Zero; $mp.Play() } }"
    )
    return [
        ["mpg123", "-q", "--loop", "-1", "-f", str(vol), filepath],
        ["vlc", "--intf", "dummy", "--repeat", "--no-video", filepath],
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_media],
    ]


def _start_audio_output(filepath, volume=0.5):
    global _file_proc
    with _file_lock:
        _stop_audio_output_unsafe()
        if not os.path.isfile(filepath):
            return
        ext = os.path.splitext(filepath)[1].lower()
        if IS_WINDOWS and ext == '.wav':
            _start_wav_windows(filepath)   # thread Python, nessun subprocess
            return
        if IS_WINDOWS:
            cmds = _win_loop_mp3_cmds(filepath, volume)
        else:
            cmds = [_aplay_loop_cmd(filepath)] if ext == '.wav' \
                   else [_mpg123_cmd(filepath, volume), _aplay_loop_cmd(filepath)]
        kw = dict(stderr=subprocess.DEVNULL)
        if not IS_WINDOWS:
            kw['start_new_session'] = True
        for cmd in cmds:
            try:
                _file_proc = subprocess.Popen(cmd, **kw)
                return
            except FileNotFoundError:
                continue


def _stop_audio_output_unsafe():
    global _file_proc
    _stop_wav_windows()  # ferma winsound se attivo
    if _file_proc and _file_proc.poll() is None:
        if IS_WINDOWS:
            # taskkill /F /T uccide il processo e tutti i suoi figli
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(_file_proc.pid)],
                    capture_output=True, timeout=5
                )
            except Exception:
                try:
                    _file_proc.kill()
                except Exception:
                    pass
        else:
            try:
                if hasattr(os, 'killpg'):
                    os.killpg(os.getpgid(_file_proc.pid), signal.SIGTERM)
                else:
                    _file_proc.terminate()
            except (ProcessLookupError, PermissionError, OSError):
                _file_proc.terminate()
            try:
                _file_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    if hasattr(os, 'killpg'):
                        os.killpg(os.getpgid(_file_proc.pid), signal.SIGKILL)
                    else:
                        _file_proc.kill()
                except Exception:
                    _file_proc.kill()
    _file_proc = None


def _stop_audio_output():
    with _file_lock:
        _stop_audio_output_unsafe()


def _detect_output_alsa(pa, out_idx):
    try:
        info = pa.get_device_info_by_index(int(out_idx))
        m = re.search(r'hw:(\d+,\d+)', info.get('name', ''))
        if m:
            return f'plughw:{m.group(1)}'
    except Exception:
        pass
    return None


# ── Entry point del sottoprocesso ─────────────────────────────────────────────
def audio_process_fn(cmd_q, data_q, cfg: dict):
    """
    Avviato con multiprocessing.get_context('spawn').Process.
    PyAudio viene inizializzato qui — nessun file descriptor ereditato.

    cfg keys:
      th_tol, th_crit        — soglie iniziali (dB)
      in_idx, out_idx        — indici dispositivo PyAudio (None = auto)
      audio_dir              — percorso cartella file audio
      tempo_check            — durata CHECK (s)
      tempo_comfort          — durata COMFORT (s)
      idle_wait              — durata IDLE_WAIT (s)
      cal_secs               — durata CALIBRATING (s)
    """
    import logging
    import pyaudio

    global _output_alsa

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [AudioWorker] %(levelname)s %(message)s',
    )
    log = logging.getLogger('audio_worker')

    # ── Config ────────────────────────────────────────────────────────────────
    th_tol        = cfg['th_tol']
    th_crit       = cfg['th_crit']
    in_idx        = cfg.get('in_idx')
    out_idx       = cfg.get('out_idx')
    audio_dir     = cfg.get('audio_dir', '/tmp')
    tempo_check   = cfg.get('tempo_check',   10)
    tempo_comfort = cfg.get('tempo_comfort', 300)
    idle_wait     = cfg.get('idle_wait',      60)
    cal_secs      = cfg.get('cal_secs',       10)

    # ── Stato FSM locale ──────────────────────────────────────────────────────
    active       = False
    mode         = "IDLE"
    countdown    = 0
    volume       = 0.5
    comfort_mode = "pink_noise"
    audio_file   = ""
    noise_det    = False
    fft_active   = True
    spectrum     = [0] * NUM_BARS
    last_mode    = None
    phase_start  = time.time()
    noise_seen   = False
    cal_samples  = []
    last_send    = time.time()
    db           = 0.0

    # ── Setup PyAudio ─────────────────────────────────────────────────────────
    pa = pyaudio.PyAudio()
    _output_alsa = _detect_output_alsa(pa, out_idx) if out_idx is not None else None
    if _output_alsa:
        log.info(f"Output ALSA: {_output_alsa}")

    in_kw = dict(format=pyaudio.paInt16, channels=1, rate=RATE,
                 input=True, output=False, frames_per_buffer=CHUNK)
    if in_idx is not None:
        in_kw['input_device_index'] = int(in_idx)

    try:
        in_stream = pa.open(**in_kw)
        log.info(f"Microfono aperto (device={in_idx})")
    except Exception as e:
        log.error(f"Errore apertura microfono: {e}")
        pa.terminate()
        return

    import atexit
    def _cleanup():
        log.info("AudioWorker cleanup: stop audio + chiusura stream")
        _stop_audio_output()
        try:
            in_stream.stop_stream()
            in_stream.close()
        except Exception:
            pass
        try:
            pa.terminate()
        except Exception:
            pass
    atexit.register(_cleanup)

    try:
        _generate_pink_noise_wav()
        log.info(f"Pink noise pronto: {PINK_WAV}")
    except Exception as e:
        log.warning(f"Pink noise fallito: {e}")

    # ── Loop principale ───────────────────────────────────────────────────────
    while True:

        # 1. Leggi comandi (non bloccante)
        try:
            while True:
                cmd = cmd_q.get_nowait()
                if cmd == "start":
                    active = True; mode = "CHECK"
                elif cmd == "stop":
                    active = False
                elif cmd == "calibrate":
                    mode = "CALIBRATING"; active = False; cal_samples = []
                elif isinstance(cmd, tuple) and len(cmd) == 2:
                    k, v = cmd
                    if   k == "volume":       volume       = float(v)
                    elif k == "comfort_mode": comfort_mode = str(v)
                    elif k == "audio_file":   audio_file   = str(v)
        except _q.Empty:
            pass

        now = time.time()

        # 2. Transizione di stato → gestisci audio output
        if mode != last_mode:
            phase_start = now
            noise_seen  = False
            if last_mode == "COMFORT":
                _stop_audio_output()
            if mode == "COMFORT":
                comfort_file = PINK_WAV
                if comfort_mode == "file" and audio_file:
                    fp = os.path.join(audio_dir, audio_file)
                    if os.path.isfile(fp):
                        comfort_file = fp
                _start_audio_output(comfort_file, volume)
            log.info(f"FSM: {last_mode} → {mode}")
            last_mode = mode

        elapsed = now - phase_start

        # 3. Lettura microfono (~64ms bloccante — cadenza del loop)
        try:
            raw  = in_stream.read(CHUNK, exception_on_overflow=False)
            data = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
            rms  = np.sqrt(np.mean(data ** 2))
            db   = float(20 * np.log10(rms / 32768.0) + 95) if rms > 0 else 0.0
            db   = round(max(0.0, db), 1)
            if fft_active:
                fft_mag  = np.abs(np.fft.rfft(data * np.hanning(len(data))))
                spectrum = []
                for lo, hi in _BINS:
                    val = float(np.mean(fft_mag[lo:hi]))
                    if np.isnan(val) or np.isinf(val) or val < 1.0:
                        spectrum.append(0)
                        continue
                    # 20-100 dB assoluto → [0, 100]: funziona per qualsiasi mic
                    band_db = 20.0 * np.log10(val)
                    bar     = int((band_db - 20.0) / 80.0 * 100.0)
                    spectrum.append(max(0, min(100, bar)))
        except Exception:
            pass

        # 4. FSM
        cal_done = None

        if mode == "IDLE":
            fft_active = True
            countdown  = 0

        elif mode == "CALIBRATING":
            fft_active = False
            countdown  = max(0, int(cal_secs - elapsed))
            if db > 0:
                cal_samples.append(db)
            if elapsed >= cal_secs:
                if cal_samples:
                    arr     = np.array(cal_samples)
                    avg     = float(np.mean(arr))
                    th_tol  = round(avg + 10.0, 1)
                    th_crit = round(avg + 20.0, 1)
                    cal_done = {"status": "ok", "avg": round(avg, 1),
                                "new_tol": th_tol, "new_crit": th_crit}
                    log.info(f"Calibra OK: avg={avg:.1f} tol={th_tol} crit={th_crit}")
                else:
                    cal_done = {"error": "Nessun campione audio"}
                cal_samples = []
                mode   = "IDLE"
                active = False

        elif mode == "CHECK":
            fft_active = False
            countdown  = max(0, int(tempo_check - elapsed))
            if db >= th_crit:
                noise_seen = True
            if elapsed >= tempo_check:
                noise_det = noise_seen
                mode      = "COMFORT" if noise_seen else "IDLE_WAIT"

        elif mode == "IDLE_WAIT":
            fft_active = True
            countdown  = max(0, int(idle_wait - elapsed))
            if elapsed >= idle_wait:
                mode = "CHECK"

        elif mode == "COMFORT":
            fft_active = True
            countdown  = max(0, int(tempo_comfort - elapsed))
            if elapsed >= tempo_comfort:
                noise_det = False
                mode      = "CHECK"

        # 4.5 Watchdog: riavvia audio se il processo è morto in COMFORT
        # _winsound_on=True significa che winsound gestisce l'audio (nessun subprocess)
        if mode == "COMFORT" and last_mode == "COMFORT":
            if not _winsound_on and (_file_proc is None or _file_proc.poll() is not None):
                log.warning("Watchdog: audio morto, riavvio")
                comfort_file = PINK_WAV
                if comfort_mode == "file" and audio_file:
                    fp = os.path.join(audio_dir, audio_file)
                    if os.path.isfile(fp):
                        comfort_file = fp
                _start_audio_output(comfort_file, volume)

        # 5. Auto-stop alle 18:00
        if active and datetime.now().hour >= 18:
            log.info("Auto-stop: orario ≥ 18:00")
            active = False

        # 6. Force-stop se utente ha premuto Ferma
        if not active and mode not in ("IDLE", "CALIBRATING"):
            _stop_audio_output()
            mode      = "IDLE"
            noise_det = False
            countdown = 0

        # 7. Invia pacchetto status ogni ~100ms (o subito se c'è cal_done)
        if now - last_send >= 0.1 or cal_done is not None:
            try:
                data_q.put_nowait({
                    "db":             db,
                    "spectrum":       spectrum,
                    "mode":           mode,
                    "countdown":      countdown,
                    "active":         active,
                    "th_tol":         th_tol,
                    "th_crit":        th_crit,
                    "noise_detected": noise_det,
                    "fft_active":     fft_active,
                    "comfort_mode":   comfort_mode,
                    "cal_done":       cal_done,
                })
            except _q.Full:
                pass
            last_send = now

