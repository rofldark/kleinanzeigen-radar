"""Telegram-Benachrichtigungen (Bot-API, nur Standardbibliothek)."""

import html
import json
import logging
import urllib.request
import uuid

from . import config
from .scraper import HEADERS

log = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{token}/{method}"


def is_configured():
    return bool(config.TELEGRAM_TOKEN and config.TELEGRAM_CHAT_ID)


def _call(method, body, content_type):
    url = API.format(token=config.TELEGRAM_TOKEN, method=method)
    req = urllib.request.Request(url, data=body, headers={"Content-Type": content_type})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def send_message(text):
    body = json.dumps({
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }).encode()
    return _call("sendMessage", body, "application/json")


def send_photo(image_url, caption):
    """Lädt das Bild selbst und schickt es als Datei – Telegram komprimiert sonst stark."""
    req = urllib.request.Request(image_url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        image = resp.read()
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in (("chat_id", config.TELEGRAM_CHAT_ID), ("parse_mode", "HTML"), ("caption", caption[:1024])):
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="anzeige.jpg"\r\n'
        f"Content-Type: image/jpeg\r\n\r\n".encode() + image + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return _call("sendPhoto", b"".join(parts), f"multipart/form-data; boundary={boundary}")


def format_listing(listing):
    e = html.escape
    lines = [f"🔍 <b>{e(listing.get('source', ''))}</b>", "", f"<b>{e(listing.get('title', ''))}</b>"]
    meta = [f"💰 {e(listing.get('price') or 'k. A.')}"]
    if listing.get("location"):
        meta.append(f"📍 {e(listing['location'])}")
    lines.append("   ".join(meta))
    if listing.get("posted"):
        lines.append(f"🕒 {e(listing['posted'])}")
    lines += ["", listing.get("url", "")]
    return "\n".join(lines)


def notify_listing(listing):
    """Schickt eine Anzeige; fällt bei Bildproblemen auf reinen Text zurück. Gibt True bei Erfolg."""
    if not is_configured():
        return False
    caption = format_listing(listing)
    try:
        if listing.get("image_large"):
            try:
                send_photo(listing["image_large"], caption)
                return True
            except Exception as exc:  # Bild nicht ladbar → Text
                log.warning("Telegram-Foto fehlgeschlagen (%s), sende Text", exc)
        send_message(caption)
        return True
    except Exception as exc:
        log.error("Telegram-Versand fehlgeschlagen: %s", exc)
        return False
