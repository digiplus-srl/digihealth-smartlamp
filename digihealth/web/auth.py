"""Autenticazione tramite API key per gli endpoint esposti su internet.

Accetta la chiave in due forme equivalenti:
  - Authorization: Bearer <token>   (preferito)
  - X-Api-Key: <token>

La chiave attesa e' letta da config.secrets.api_key (variabile DIGIHEALTH_API_KEY).
"""
import hmac
from functools import wraps
from flask import request, jsonify
from ..config import config
from ..logger import logger


def _extract_key() -> str | None:
    """Estrae la chiave dall'header Authorization (Bearer) o X-Api-Key."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer "):].strip()
    api_key = request.headers.get("X-Api-Key")
    if api_key:
        return api_key.strip()
    return None


def require_api_key(func):
    """Decorator: blocca con 401 se la API key e' mancante o errata."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        expected = config.secrets.api_key
        if not expected:
            # Fail-closed: senza chiave configurata non si espone nulla.
            logger.error("DIGIHEALTH_API_KEY non configurata: richiesta rifiutata.")
            return jsonify({"status": "error", "message": "Server misconfigured"}), 503

        provided = _extract_key()
        # Confronto a tempo costante per evitare timing attack.
        if not provided or not hmac.compare_digest(provided, expected):
            logger.warning(f"Tentativo non autorizzato da {request.remote_addr} su {request.path}")
            return jsonify({"status": "error", "message": "Unauthorized"}), 401

        return func(*args, **kwargs)
    return wrapper
