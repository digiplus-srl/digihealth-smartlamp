import os, threading, logging, sys, subprocess, time
import multiprocessing
import platform as _plat
import queue as _q
import yaml

_LAUNCH_DIR = os.getcwd()
from flask import Flask, render_template, jsonify, request
from ..config import config
from ..logger import logger
from ..audio_worker import audio_process_fn

_CFG_PATH = os.environ.get(
    'DIGIHEALTH_CONFIG',
    'config/windows.yaml' if _plat.system() == 'Windows' else 'config/default.yaml'
)

logging.getLogger('werkzeug').setLevel(logging.ERROR)

base_dir     = os.path.dirname(os.path.abspath(__file__))
template_dir = os.path.join(base_dir, 'templates')
AUDIO_DIR    = os.path.normpath(os.path.join(base_dir, '..', '..', 'audio'))

app = Flask(__name__, template_folder=template_dir)

# ── Config audio ──────────────────────────────────────────────────────────────
_ac_cfg = config.processors.audio_comfort
NUM_BARS = 48

# ── Stato Flask (aggiornato dal queue-reader) ─────────────────────────────────
state = {
    "active":         False,
    "mode":           "IDLE",
    "countdown":      0,
    "level":          0.0,
    "th_tol":         float(_ac_cfg.get('tolerance_threshold', 45.0)),
    "th_crit":        float(_ac_cfg.get('critical_threshold',  65.0)),
    "volume":         0.5,
    "spectrum":       [0] * NUM_BARS,
    "comfort_mode":   "pink_noise",
    "audio_file":     "",
    "air_quality":    {"temp": "--", "humidity": "--", "co2": "--", "iaqi": "--"},
    "noise_detected": False,
    "fft_active":     True,
    "actuators":      {},
    # Ultimo evento di alert ricevuto e processato dal dispatcher.
    # La dashboard lo legge per mostrare il toast. Schema:
    # {ts, id, level, dominant_pollutant, action_code, targets[], hold_seconds}
    "last_alert_event": None,
}


def set_alert_event(alert_id, level, dominant_pollutant, action_code, targets, hold_seconds):
    """Pubblica un evento di alert per la dashboard (toast notification).
    Chiamato dal dispatcher dopo l'esecuzione dell'azione."""
    state["last_alert_event"] = {
        "ts": time.time(),
        "id": alert_id,
        "level": level or "INFO",
        "dominant_pollutant": dominant_pollutant or "",
        "action_code": action_code or "",
        "targets": list(targets) if targets else [],
        "hold_seconds": float(hold_seconds) if hold_seconds else 0.0,
    }

# ── IPC ───────────────────────────────────────────────────────────────────────
_cmd_queue:  multiprocessing.Queue   = None
_data_queue: multiprocessing.Queue   = None
_audio_proc: multiprocessing.Process = None
_cal_result = {"status": "idle"}

# Riferimento all'ActuatorManager vivo, usato da /status per restituire lo
# stato attuatori in tempo reale (vedi WebManager.set_actuator_manager).
_actuator_manager = None


# ── Queue reader (thread nel processo principale) ─────────────────────────────
def _queue_reader():
    global _cal_result
    while True:
        try:
            pkt = _data_queue.get(timeout=1.0)
            state["level"]          = pkt["db"]
            state["spectrum"]       = pkt["spectrum"]
            state["mode"]           = pkt["mode"]
            state["countdown"]      = pkt["countdown"]
            state["active"]         = pkt["active"]
            state["th_tol"]         = pkt["th_tol"]
            state["th_crit"]        = pkt["th_crit"]
            state["noise_detected"] = pkt["noise_detected"]
            state["fft_active"]     = pkt["fft_active"]
            state["comfort_mode"]   = pkt["comfort_mode"]
            if pkt.get("cal_done"):
                _cal_result = pkt["cal_done"]
        except (_q.Empty, Exception):
            pass


def _send_cmd(cmd):
    if _cmd_queue is not None:
        try:
            _cmd_queue.put_nowait(cmd)
        except _q.Full:
            logger.warning(f"cmd_queue piena: {cmd}")


# ── Route Flask ───────────────────────────────────────────────────────────────
@app.route('/')
def index():
    tags = config.communicator.telegraf.get("tags", {})
    return render_template('dashboard.html',
        device_lampada=tags.get("lampada", ""),
        device_stanza=tags.get("stanza", ""),
    )


@app.route('/status')
def get_status():
    # Stato attuatori LIVE: legge dalle istanze in memoria (is_on,
    # override_active, ecc. sono cached, niente I/O verso i device), cosi'
    # le forzature da alert appaiono in dashboard immediatamente invece di
    # aspettare il prossimo tick del main loop sensori (~30s).
    if _actuator_manager is not None:
        try:
            state["actuators"] = _actuator_manager.get_status()
        except Exception as e:
            logger.debug(f"/status: get_status live fallito: {e}")
    return jsonify(state)


@app.route('/toggle')
def toggle():
    if not state["active"]:
        _send_cmd("start")
        logger.info("▶ AVVIA")
    else:
        _send_cmd("stop")
        logger.info("■ FERMA")
    return jsonify({"status": "ok"})


@app.route('/calibrate')
def calibrate():
    global _cal_result
    _cal_result = {"status": "running"}
    _send_cmd("calibrate")
    return jsonify({"status": "started"})


@app.route('/calibrate/result')
def calibrate_result():
    return jsonify(_cal_result)


@app.route('/set_comfort_mode', methods=['POST'])
def set_comfort_mode():
    d = request.get_json(silent=True) or {}
    m = d.get('mode', 'pink_noise')
    if m in ('pink_noise', 'file'):
        _send_cmd(("comfort_mode", m))
        return jsonify({"status": "ok"})
    return jsonify({"status": "error"}), 400


@app.route('/set_audio_file', methods=['POST'])
def set_audio_file():
    d  = request.get_json(silent=True) or {}
    fn = d.get('filename', '')
    fp = os.path.join(AUDIO_DIR, fn)
    if fn and os.path.isfile(fp):
        _send_cmd(("audio_file", fn))
        return jsonify({"status": "ok"})
    return jsonify({"status": "error", "message": "File non trovato"}), 404


@app.route('/audio_files')
def audio_files():
    os.makedirs(AUDIO_DIR, exist_ok=True)
    files = sorted([f for f in os.listdir(AUDIO_DIR)
                    if f.lower().endswith(('.mp3', '.wav', '.ogg', '.flac'))])
    return jsonify({"files": files})


@app.route('/set_volume', methods=['POST', 'GET'])
def set_volume():
    vol = None
    if request.method == 'POST':
        vol = (request.get_json(silent=True) or {}).get('volume')
    else:
        vol = request.args.get('volume', type=float)
    if vol is not None:
        vol = max(0.0, min(1.0, float(vol)))
        state["volume"] = vol
        _send_cmd(("volume", vol))
        return jsonify({"status": "ok", "volume": vol})
    return jsonify({"status": "error"}), 400


@app.route('/shutdown_kiosk')
def shutdown_kiosk():
    os.system("pkill chromium")
    return "Closing..."


@app.route('/config')
def config_page():
    return render_template('config.html')


@app.route('/thresholds')
def thresholds_page():
    return render_template('thresholds.html')


@app.route('/thresholds/data')
def thresholds_data():
    try:
        with open(_CFG_PATH, encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
        return jsonify(data.get('thresholds', {}))
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/thresholds/save', methods=['POST'])
def thresholds_save():
    try:
        new_thr = request.get_json(silent=True)
        if new_thr is None:
            return jsonify({'status': 'error', 'message': 'Payload non valido'}), 400
        with open(_CFG_PATH, encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
        data['thresholds'] = new_thr
        with open(_CFG_PATH, 'w', encoding='utf-8') as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        logger.info(f"Soglie salvate su {_CFG_PATH}")
        return jsonify({'status': 'ok'})
    except Exception as e:
        logger.warning(f"thresholds_save errore: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/config/data')
def config_data():
    try:
        with open(_CFG_PATH, encoding='utf-8') as f:
            data = yaml.safe_load(f)
        return jsonify(data or {})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/restart', methods=['POST'])
def restart():
    def _do():
        time.sleep(0.6)
        subprocess.Popen(
            [sys.executable, '-m', 'digihealth.main'],
            cwd=_LAUNCH_DIR
        )
        os._exit(0)
    threading.Thread(target=_do, daemon=True).start()
    return jsonify({'status': 'ok'})


@app.route('/config/save', methods=['POST'])
def config_save():
    try:
        data = request.get_json(silent=True)
        if data is None:
            return jsonify({'status': 'error', 'message': 'Payload non valido'}), 400
        with open(_CFG_PATH, 'w', encoding='utf-8') as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        logger.info(f"Config salvata su {_CFG_PATH}")
        return jsonify({'status': 'ok'})
    except Exception as e:
        logger.warning(f"config_save errore: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ── Pagina log alert/azioni (LAN, non esposta dal tunnel) ─────────────────────
@app.route('/alerts')
def alerts_page():
    return render_template('alerts.html')


@app.route('/alerts/data')
def alerts_data():
    from . import storage
    from .api import EXPECTED_LAMPADA
    # Mostriamo solo gli alert destinati a questa lampada (o senza campo
    # lampada, per compatibilita'). Gli altri restano su SQLite ma non
    # vengono elencati in dashboard.
    expected = EXPECTED_LAMPADA.upper()
    rows = [
        a for a in storage.list_alerts(200)
        if not (a.get("lampada") or "").strip()
        or (a.get("lampada") or "").strip().upper() == expected
    ][:100]
    return jsonify({"alerts": rows})


# ── WebManager ────────────────────────────────────────────────────────────────
class WebManager:
    def __init__(self):
        self.host = config.web.host
        self.port = config.web.port

    def set_mic_sensor(self, _):
        pass

    def get_status(self):
        return state

    def update_actuators(self, actuators_status: dict):
        state["actuators"] = actuators_status

    def set_actuator_manager(self, actuator_manager):
        """Collega l'ActuatorManager vivo al dispatcher dell'API di ingestion
        e alla route /status per restituire stato in tempo reale."""
        global _actuator_manager
        _actuator_manager = actuator_manager
        from .api import dispatcher
        dispatcher.bind(actuator_manager)

    def update_data(self, processed_data: dict):
        try:
            t = processed_data.get('TEMP-[C]')
            state["air_quality"] = {
                "temp":     round(float(t), 1) if t not in (None, '--') else '--',
                "humidity": processed_data.get('HUM-[%]', '--'),
                "co2":      processed_data.get('CO2-AnidrideCarbonica-[ppm]', '--'),
                "tvoc":     processed_data.get('TVOC-QualitaAria-[G]', '--'),
                "lux":      processed_data.get('lux-IntensitaLuminosa', '--'),
                "iaqi":     processed_data.get('IAQI', '--'),
            }
        except Exception as e:
            logger.error(f"update_data: {e}")

    def run(self):
        global _cmd_queue, _data_queue, _audio_proc
        os.makedirs(AUDIO_DIR, exist_ok=True)

        if config.sensors.microphone.get('enabled', False):
            mic_cfg = config.sensors.microphone

            # spawn: il figlio parte come interprete Python pulito.
            # Nessun lock ereditato dai thread sensori/comunicatore del padre.
            # PyAudio viene inizializzato dentro audio_process_fn, mai qui.
            _mp_ctx     = multiprocessing.get_context('spawn')
            _cmd_queue  = _mp_ctx.Queue()
            _data_queue = _mp_ctx.Queue(maxsize=20)

            worker_cfg = {
                "th_tol":        state["th_tol"],
                "th_crit":       state["th_crit"],
                "in_idx":        mic_cfg.get('device_index', None),
                "out_idx":       mic_cfg.get('output_device_index', None),
                "audio_dir":     AUDIO_DIR,
                "tempo_check":   int(_ac_cfg.get('check_duration',   10)),
                "tempo_comfort": int(_ac_cfg.get('comfort_duration', 300)),
                "idle_wait":     60,
                "cal_secs":      10,
            }

            _audio_proc = _mp_ctx.Process(
                target=audio_process_fn,
                args=(_cmd_queue, _data_queue, worker_cfg),
                daemon=True,
                name="AudioWorker",
            )
            _audio_proc.start()
            logger.info(f"AudioWorker avviato (pid={_audio_proc.pid})")

            threading.Thread(target=_queue_reader, daemon=True,
                             name="QueueReader").start()

        app.run(host=self.host, port=self.port,
                debug=False, use_reloader=False, threaded=True)


# ── API di ingestion (esposta via Cloudflare Tunnel) ──────────────────────────
# Aggiunge gli endpoint /api/* (ricezione alert) all'app esistente, senza
# toccare dashboard, /config e /thresholds.
from . import storage as _ingestion_storage
from .api import api_bp as _api_bp

_ingestion_storage.init_db()
app.register_blueprint(_api_bp)
