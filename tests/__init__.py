"""Tests dürfen nie echte Telegram-Nachrichten verschicken.

Sind TELEGRAM_TOKEN und TELEGRAM_CHAT_ID gesetzt (Umgebung oder .env), würden ohne diese Sperre
Testanzeigen im echten Chat landen.
"""

from app import config, telegram

config.TELEGRAM_TOKEN = ""
config.TELEGRAM_CHAT_ID = ""


def _blocked(*args, **kwargs):
    raise AssertionError("Telegram-Versand in Tests ist gesperrt")


telegram._call = _blocked
