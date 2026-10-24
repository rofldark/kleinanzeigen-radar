#!/usr/bin/env python3
"""Startpunkt: python main.py"""

import logging

from app import VERSION, config
from app.history import History
from app.monitor import Monitor
from app.server import serve
from app.store import Store


def open_history(store, log):
    """Öffnet den Suchverlauf; übernimmt beim ersten Mal die vorhandene Trefferliste."""
    try:
        history = History(config.DATA_DIR / "history.db")
        if history.is_empty() and store.listings:
            history.import_listings(store.get_config()["searches"], store.snapshot()["listings"])
            log.info("Verlauf: %d vorhandene Anzeigen übernommen", len(store.listings))
        return history
    except Exception:
        log.exception("Verlauf (history.db) nicht verfügbar – Überwachung läuft ohne")
        return None


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("kleinanzeigen-radar")
    log.info("Kleinanzeigen-Radar v%s – Daten in %s", VERSION, config.DATA_DIR)
    if not config.TELEGRAM_TOKEN or not config.TELEGRAM_CHAT_ID:
        log.warning("Telegram nicht konfiguriert – es werden keine Nachrichten verschickt")
    store = Store()
    history = open_history(store, log)
    monitor = Monitor(store, history)
    monitor.start_thread()
    serve(store, monitor)


if __name__ == "__main__":
    main()
