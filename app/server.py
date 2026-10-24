"""HTTP-Server: liefert die Oberfläche aus und stellt die JSON-API bereit."""

import json
import logging
import mimetypes
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import VERSION, config, scraper, telegram

log = logging.getLogger(__name__)


def make_handler(store, monitor):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Kleinanzeigen-Radar"

        def log_message(self, fmt, *args):
            log.debug("%s - %s", self.address_string(), fmt % args)

        # ── Antworten ──────────────────────────────
        def _send(self, status, body, content_type, cache="no-store", headers=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, data, status=200):
            self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _body(self):
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            try:
                data = json.loads(self.rfile.read(length))
            except ValueError:
                return None
            return data if isinstance(data, dict) else None

        def _static(self, rel):
            path = (config.STATIC_DIR / rel).resolve()
            if config.STATIC_DIR.resolve() not in path.parents or not path.is_file():
                return self._send(404, b"Nicht gefunden", "text/plain; charset=utf-8")
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript",):
                ctype += "; charset=utf-8"
            body = path.read_bytes()
            if rel == "index.html":
                body = body.replace(b"__VERSION__", VERSION.encode())
            self._send(200, body, ctype, cache="no-cache")

        # ── Routen ─────────────────────────────────
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            url = urlparse(self.path)
            if url.path in ("/", "/index.html"):
                return self._static("index.html")
            if url.path.startswith("/static/"):
                return self._static(url.path[len("/static/"):])
            if url.path == "/api/health":
                ok = monitor.healthy()
                return self._json({"ok": ok, "version": VERSION}, 200 if ok else 503)
            if url.path == "/api/locations":
                query = parse_qs(url.query).get("q", [""])[0]
                try:
                    return self._json({"locations": scraper.location_suggestions(query)[:15]})
                except scraper.ScrapeError as exc:
                    return self._json({"error": f"Ortssuche fehlgeschlagen: {exc}"}, 502)
            if url.path == "/api/config":
                return self._json(self._config_payload())
            if url.path == "/api/state":
                since = parse_qs(url.query).get("rev", [""])[0]
                return self._json(self._state_payload(since))
            if url.path.startswith("/api/history/"):
                return self._history(url.path[len("/api/history/"):], parse_qs(url.query))
            self._send(404, b"Nicht gefunden", "text/plain; charset=utf-8")

        def _history(self, route, qs):
            history = monitor.history
            if history is None:
                return self._json({"error": "Suchverlauf (history.db) ist nicht verfügbar."}, 503)

            def arg(name, default=None):
                return (qs.get(name, [""])[0].strip() or default)

            try:
                page = int(arg("page", "1"))
            except ValueError:
                page = 1
            try:
                if route == "stats":
                    return self._json(history.stats())
                if route == "listings":
                    return self._json(history.query_listings(
                        search_id=arg("search"), text=arg("q"), status=arg("status"),
                        sort=arg("sort", "new"), page=page))
                if route == "listing":
                    item = history.listing_detail(arg("id", ""))
                    return self._json(item) if item else self._json({"error": "Anzeige nicht gefunden"}, 404)
                if route == "changes":
                    return self._json(history.query_changes(search_id=arg("search"), field=arg("field"), page=page))
                if route == "runs":
                    return self._json(history.query_runs(search_id=arg("search"),
                                                         errors_only=arg("errors") == "1", page=page))
                if route == "download":
                    stamp = time.strftime("%Y%m%d-%H%M")
                    return self._send(200, history.export_bytes(), "application/vnd.sqlite3",
                                      headers={"Content-Disposition":
                                               f'attachment; filename="kleinanzeigen-history-{stamp}.db"'})
            except Exception as exc:
                log.exception("Verlauf-Abfrage fehlgeschlagen")
                return self._json({"error": f"Datenbankfehler: {exc}"}, 500)
            return self._json({"error": "Unbekannter Endpunkt"}, 404)

        def do_POST(self):
            path = urlparse(self.path).path
            body = self._body()
            if body is None:
                return self._json({"error": "Ungültige Anfrage"}, 400)
            if path == "/api/config":
                store.update_config(body)
                return self._json(self._config_payload())
            if path == "/api/start":
                if body:
                    store.update_config(body)
                cfg = store.get_config()
                if not any(s["enabled"] for s in cfg["searches"]):
                    return self._json({"error": "Bitte mindestens eine Suche aktivieren."}, 400)
                monitor.start()
                return self._json({"ok": True})
            if path == "/api/stop":
                monitor.stop()
                return self._json({"ok": True})
            if path == "/api/scan":
                monitor.scan_now()
                return self._json({"ok": True})
            if path == "/api/clear":
                store.clear_listings()
                return self._json({"ok": True})
            if path == "/api/seen":
                if isinstance(body.get("ids"), list):
                    store.mark_read(body["ids"][:1000])
                else:
                    store.mark_all_read()
                return self._json({"ok": True})
            if path == "/api/telegram/test":
                if not telegram.is_configured():
                    return self._json({"error": "Telegram-Token / Chat-ID fehlen (TELEGRAM_TOKEN / TELEGRAM_CHAT_ID setzen)"}, 400)
                try:
                    telegram.send_message("✅ <b>Kleinanzeigen-Radar</b>: Telegram-Verbindung funktioniert.")
                except Exception as exc:
                    return self._json({"error": f"Telegram-Fehler: {exc}"}, 502)
                return self._json({"ok": True})
            self._json({"error": "Unbekannter Endpunkt"}, 404)

        # ── Nutzdaten ──────────────────────────────
        def _config_payload(self):
            return {
                "config": store.get_config(),
                "radius_options": scraper.RADIUS_OPTIONS,
                "min_interval": config.MIN_INTERVAL,
                "version": VERSION,
            }

        def _state_payload(self, since_rev):
            data = monitor.status()
            snap = store.snapshot()
            data["rev"] = snap["rev"]
            data["count"] = len(snap["listings"])
            data["unread"] = snap["unread"]
            if since_rev != str(snap["rev"]):
                data["listings"] = snap["listings"]
            return data

    return Handler


def serve(store, monitor):
    httpd = ThreadingHTTPServer(("0.0.0.0", config.PORT), make_handler(store, monitor))
    httpd.daemon_threads = True
    log.info("Weboberfläche: http://localhost:%d", config.PORT)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
