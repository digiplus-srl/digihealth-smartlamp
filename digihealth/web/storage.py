"""Persistenza degli alert ricevuti su SQLite locale.

Il path del database e' preso da config.communicator.ipc.db_path.
Ogni alert viene salvato con il JSON grezzo integrale piu' alcune colonne
estratte per facilitare query e debug. `processed` indica se l'azione locale
associata e' gia' stata gestita (in v1 e' solo loggata).
"""
import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..config import config
from ..logger import logger
from .schemas import NormalizedAlert

_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at        TEXT NOT NULL,
    event_type         TEXT,
    schema_version     TEXT,
    timestamp          TEXT,
    client_id          TEXT,
    lampada            TEXT,
    stanza             TEXT,
    host               TEXT,
    trigger_metric     TEXT,
    trigger_value      REAL,
    level              TEXT,
    overall_status     TEXT,
    dominant_pollutant TEXT,
    action_code        TEXT,
    recommended_action TEXT,
    urgency            TEXT,
    processed          INTEGER NOT NULL DEFAULT 0,
    action_taken       TEXT,
    raw_json           TEXT NOT NULL
);
"""


def _db_path() -> str:
    return config.communicator.ipc.get("db_path", "/tmp/digihealth.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Crea la tabella se non esiste. Da chiamare all'avvio del WebManager."""
    with _lock, _connect() as conn:
        conn.execute(_SCHEMA)
        # Migrazione: aggiunge colonne nuove a DB creati prima di questa versione.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(alerts)")}
        for col in ("action_taken", "dominant_pollutant"):
            if col not in cols:
                conn.execute(f"ALTER TABLE alerts ADD COLUMN {col} TEXT")
    logger.info(f"Storage alert inizializzato: {_db_path()}")


def save_alert(alert: NormalizedAlert) -> int:
    """Salva un alert normalizzato e restituisce l'id assegnato."""
    received_at = datetime.now(timezone.utc).isoformat()
    with _lock, _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO alerts (
                received_at, event_type, schema_version, timestamp,
                client_id, lampada, stanza, host,
                trigger_metric, trigger_value, level, overall_status,
                dominant_pollutant, action_code, recommended_action, urgency,
                processed, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                received_at, alert.event_type, alert.schema_version, alert.timestamp,
                alert.client_id, alert.lampada, alert.stanza, alert.host,
                alert.trigger_metric, alert.trigger_value, alert.level, alert.overall_status,
                alert.dominant_pollutant, alert.action_code, alert.recommended_action,
                alert.urgency,
                json.dumps(alert.raw, ensure_ascii=False),
            ),
        )
        return cur.lastrowid


def mark_processed(alert_id: int, action_taken: Optional[str] = None) -> None:
    """Marca un alert come gestito e registra l'azione eseguita."""
    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE alerts SET processed = 1, action_taken = ? WHERE id = ?",
            (action_taken, alert_id),
        )


def list_alerts(limit: int = 50) -> List[Dict[str, Any]]:
    """Ultimi alert ricevuti (per dashboard/debug)."""
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]
