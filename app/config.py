"""Konfiguration aus Umgebungsvariablen (lokal zusätzlich aus einer .env im Projektordner)."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env(name, default=""):
    return os.environ.get(name, default).strip()


def _load_dotenv():
    """Liest eine lokale .env (nur für den Start ohne Docker). Bereits gesetzte Variablen gewinnen."""
    path = BASE_DIR / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

PORT = int(_env("PORT", "8080"))
DATA_DIR = Path(_env("DATA_DIR", str(BASE_DIR / "data")))
STATIC_DIR = BASE_DIR / "static"

# Telegram ist optional – ohne Token und Chat-ID werden keine Nachrichten verschickt
TELEGRAM_TOKEN = _env("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = _env("TELEGRAM_CHAT_ID")

# Obergrenzen, damit die Datendatei nicht endlos wächst
MAX_LISTINGS = int(_env("MAX_LISTINGS", "600"))
MAX_SEEN_IDS = int(_env("MAX_SEEN_IDS", "20000"))
# Maximal so viele Telegram-Nachrichten pro Suchlauf
MAX_TELEGRAM_PER_RUN = int(_env("MAX_TELEGRAM_PER_RUN", "10"))
# Kürzestes erlaubtes Intervall – schützt vor Sperren durch Kleinanzeigen
MIN_INTERVAL = int(_env("MIN_INTERVAL", "30"))
# Anzeigen, die älter inseriert wurden, gelten nie als „neu“ (Schutz vor nachrutschenden Altanzeigen)
MAX_AGE_DAYS = int(_env("MAX_AGE_DAYS", "2"))
# Parser-Wächter: so viele verdächtige Leerergebnisse bzw. Fehler in Folge lösen eine Warnung aus
WATCHDOG_EMPTY_RUNS = int(_env("WATCHDOG_EMPTY_RUNS", "2"))
WATCHDOG_ERROR_RUNS = int(_env("WATCHDOG_ERROR_RUNS", "3"))
