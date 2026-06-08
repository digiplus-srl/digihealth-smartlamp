"""Invio notifiche Telegram per gli alert DigiHealth.

Legge BOT_TOKEN e CHAT_ID esclusivamente da variabili d'ambiente.
Non solleva mai eccezioni verso il chiamante: un errore Telegram non deve
bloccare il dispatch delle azioni fisiche.
"""
import os
import requests
from ..logger import logger

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

LEVEL_EMOJI = {
    "CRITICAL": "\U0001f534",   # 🔴
    "WARNING":  "\U0001f7e0",   # 🟠
    "WARN":     "\U0001f7e0",
    "INFO":     "\U0001f535",   # 🔵
}

CATEGORY_LABEL = {
    "air":  "Qualita aria",
    "temp": "Temperatura",
}


def _build_message(alert, alert_id: int, summary: str, category: str) -> str:
    level = (alert.level or "INFO").upper()
    emoji = LEVEL_EMOJI.get(level, "")
    pollutant = alert.dominant_pollutant or alert.trigger_metric or "N/D"
    stanza = alert.stanza or "N/D"
    action = alert.recommended_action or alert.action_code or "N/D"
    categoria = CATEGORY_LABEL.get(category, "Generico")

    lines = [
        f"{emoji} *Alert DigiHealth* {emoji}",
        f"*Livello:* {level}",
        f"*Categoria:* {categoria}",
        f"*Stanza:* {stanza}",
        f"*Inquinante/metrica:* {pollutant}",
        f"*Valore trigger:* {alert.trigger_value or 'N/D'}",
        f"*Azione consigliata:* {action}",
        f"*Azioni eseguite:* {summary}",
        f"_ID alert: {alert_id}_",
    ]
    return "\n".join(lines)


def send_alert(alert, alert_id: int, summary: str, category: str = "") -> bool:
    """Invia un messaggio Telegram per l'alert dato. Ritorna True se ok."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        logger.debug("Telegram: TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID non impostati, notifica saltata.")
        return False

    text = _build_message(alert, alert_id, summary, category)
    url = TELEGRAM_API.format(token=token)

    try:
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
        if resp.ok:
            logger.info("Telegram: notifica inviata per alert id=%s", alert_id)
            return True
        else:
            logger.warning("Telegram: risposta non ok (%s): %s", resp.status_code, resp.text[:200])
            return False
    except requests.RequestException as e:
        logger.warning("Telegram: errore di rete: %s", e)
        return False
