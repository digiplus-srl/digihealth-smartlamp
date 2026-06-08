"""Blueprint API di ingestion, esposto su internet via Cloudflare Tunnel.

Riceve gli alert HTTP POST dal sistema esterno (predizione qualita' aria / CRM),
li autentica (API key), li valida, li salva su SQLite ed esegue l'azione locale
sugli attuatori tramite il dispatcher.
"""
import threading
from flask import Blueprint, jsonify, request
from ..logger import logger
from ..config import config
from .auth import require_api_key
from .schemas import parse_alert, ValidationError
from . import storage
from .dispatcher import ActionDispatcher

api_bp = Blueprint("api", __name__, url_prefix="/api")

# Istanza condivisa: WebManager.set_actuator_manager() la collega agli attuatori.
dispatcher = ActionDispatcher()

# ID della lampada gestita da questo Raspberry, letto dalla config (telegraf.tags.lampada).
# Gli alert con campo "lampada" diverso vengono salvati ma non azionano gli attuatori.
EXPECTED_LAMPADA = (config.communicator.telegraf.get("tags", {}).get("lampada") or "").strip()


def _client_ip() -> str:
    """IP reale del mittente. Dietro Cloudflare Tunnel, request.remote_addr e'
    127.0.0.1 (cloudflared locale): l'IP del client vero arriva in
    CF-Connecting-IP. Fallback su X-Forwarded-For e poi remote_addr."""
    return (
        request.headers.get("CF-Connecting-IP")
        or (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        or request.remote_addr
        or "?"
    )


@api_bp.route("/health")
def health():
    """Health check senza autenticazione (per monitor Cloudflare/uptime)."""
    return jsonify({"status": "ok"}), 200


@api_bp.route("/alerts", methods=["POST"])
@require_api_key
def receive_alert():
    """Riceve un alert, lo logga su SQLite e accoda l'azione locale."""
    client_ip = _client_ip()

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"status": "error", "message": "JSON body richiesto"}), 400

    try:
        alert = parse_alert(payload)
    except ValidationError as e:
        logger.warning(f"Alert non valido da {client_ip}: {e}")
        return jsonify({"status": "error", "message": "Payload non valido", "details": e.errors()}), 400

    alert_id = storage.save_alert(alert)

    # Filtro dispositivo: ignoriamo gli alert destinati ad altre lampade.
    # Se il payload non specifica "lampada" lo lasciamo passare (compatibilita').
    incoming = (alert.lampada or "").strip()
    if incoming and incoming.upper() != EXPECTED_LAMPADA.upper():
        storage.mark_processed(alert_id, action_taken=f"ignored:device_mismatch({incoming})")
        # Volutamente DEBUG: il log INFO mostra solo gli alert per questa lampada.
        logger.debug(
            f"Alert id={alert_id} ignorato: lampada={incoming!r} "
            f"!= atteso {EXPECTED_LAMPADA!r} (da {client_ip})"
        )
        return jsonify({
            "status": "ignored",
            "reason": "device_mismatch",
            "id": alert_id,
        }), 200

    # Azione sugli attuatori in background: i comandi ai device Tuya via LAN
    # possono richiedere qualche secondo, ma al mittente rispondiamo subito 2xx.
    threading.Thread(
        target=dispatcher.dispatch, args=(alert, alert_id, client_ip),
        daemon=True, name=f"dispatch-{alert_id}",
    ).start()

    logger.info(
        f"Alert ricevuto id={alert_id} type={alert.event_type} "
        f"level={alert.level} action={alert.action_code} da {client_ip}"
    )
    return jsonify({"status": "received", "id": alert_id}), 200
