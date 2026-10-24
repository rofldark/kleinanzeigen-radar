"""Hintergrund-Überwachung: fragt alle aktiven Suchen im Zufalls-Intervall ab.

Enthält den Parser-Wächter gegen stille Ausfälle: Liefert eine Suche wiederholt 0 Anzeigen,
obwohl Kleinanzeigen nicht „keine Ergebnisse“ meldet, fehlen plötzlich Preise, scheitern Abrufe
dauerhaft oder gab es lange keinen erfolgreichen Suchlauf, erscheint eine Warnung in der
Oberfläche und per Telegram (einmalig, mit Entwarnung, sobald es wieder passt).
"""

import logging
import random
import threading
import time

from . import config, scraper, telegram

log = logging.getLogger(__name__)


def search_request_url(search):
    if search["type"] == "url":
        return search["url"]
    return scraper.search_url(search["query"], search.get("location"),
                              search.get("price_min"), search.get("price_max"))


def active_sources(cfg):
    """Liefert (Quellschlüssel, Suche, URL) aller aktiven Suchen.

    Der Schlüssel enthält die URL: Ändern sich Ort oder Preis, wird der Bestand neu eingelesen.
    """
    out = []
    for s in cfg.get("searches", []):
        if s.get("enabled"):
            url = search_request_url(s)
            out.append((f"{s['id']}|{url}", s, url))
    return out


def passes_filters(listing, search, cfg):
    """Globale und suchspezifische Filter – dieselbe Logik wie `passesFilters` in static/app.js."""
    title = listing.get("title", "").lower()
    if any(w in title for w in cfg.get("blacklist", [])):
        return False
    if cfg.get("hide_requests") and listing.get("is_request"):
        return False
    if search is None:
        return True
    if any(w in title for w in search.get("exclude", [])):
        return False
    must = search.get("must_contain", [])
    if must and not any(w in title for w in must):
        return False
    price = listing.get("price_value")
    if price is None:
        return search.get("show_no_price", True)
    if search.get("price_min") is not None and price < search["price_min"]:
        return False
    if search.get("price_max") is not None and price > search["price_max"]:
        return False
    return True


class Monitor:
    def __init__(self, store, history=None):
        self.store = store
        self.history = history        # optional: Verlauf aller Suchergebnisse (app.history)
        self._wake = threading.Event()
        self.scanning = False
        self.started_ts = time.time()
        self.last_check_ts = None
        self.last_success_ts = None   # letzter Lauf, in dem mindestens eine Suche Anzeigen lieferte
        self.next_check_ts = None
        self.last_new_count = 0
        self.errors = {}              # Suchname → Fehlermeldung des letzten Laufs
        self.health = {}              # Such-ID → Zustand für den Wächter
        self.alerts = {}              # aktive Warnungen: Schlüssel → Text
        self.blocked_until = None
        self._thread = threading.Thread(target=self._loop, name="monitor", daemon=True)

    def start_thread(self):
        self._thread.start()

    # ── Steuerung ──────────────────────────────────
    def start(self):
        self.store.set_running(True)
        self.started_ts = time.time()
        self.next_check_ts = time.time()
        self._wake.set()

    def stop(self):
        self.store.set_running(False)
        self.next_check_ts = None
        self._alert_clear("stale")
        self._wake.set()

    def scan_now(self):
        self.next_check_ts = time.time()
        self._wake.set()

    def _stale_limit(self, cfg):
        return max(3 * cfg["interval_max"], 30 * 60)

    def healthy(self):
        """Für den Docker-Healthcheck: Thread lebt und die Überwachung hängt nicht."""
        if not self._thread.is_alive():
            return False
        return "stale" not in self.alerts

    def status(self):
        cfg = self.store.get_config()
        running = cfg["running"]
        if not running:
            state = "idle"
        elif self.blocked_until and self.blocked_until > time.time():
            state = "blocked"
        elif self.alerts:
            state = "warning"
        elif self.errors:
            state = "error"
        else:
            state = "running"
        return {
            "status": state,
            "running": running,
            "scanning": self.scanning,
            "last_check_ts": self.last_check_ts,
            "last_success_ts": self.last_success_ts,
            "next_check_ts": self.next_check_ts if running else None,
            "last_new_count": self.last_new_count,
            "errors": self.errors,
            "warnings": list(self.alerts.values()),
            "health": self.health,
            "telegram_configured": telegram.is_configured(),
        }

    # ── Warnungen ──────────────────────────────────
    def _alert(self, key, text):
        if key in self.alerts:
            return
        self.alerts[key] = text
        log.warning("Warnung: %s", text)
        self._send_system(f"⚠️ <b>Kleinanzeigen-Radar</b>\n{text}")

    def _alert_clear(self, key, text=None):
        if self.alerts.pop(key, None) is not None and text:
            log.info("Entwarnung: %s", text)
            self._send_system(f"✅ <b>Kleinanzeigen-Radar</b>\n{text}")

    @staticmethod
    def _send_system(text):
        # Systemwarnungen gehen immer raus, wenn Telegram eingerichtet ist
        if not telegram.is_configured():
            return
        try:
            telegram.send_message(text)
        except Exception as exc:
            log.error("Telegram-Warnung fehlgeschlagen: %s", exc)

    def _check_stale(self, cfg):
        if not cfg["running"] or self.scanning:
            return
        reference = max(self.last_success_ts or 0, self.started_ts)
        if time.time() - reference > self._stale_limit(cfg):
            minutes = int((time.time() - reference) / 60)
            self._alert("stale", f"Seit {minutes} Minuten kein erfolgreicher Suchlauf. Bitte Weboberfläche prüfen.")

    # ── Schleife ───────────────────────────────────
    def _loop(self):
        if self.store.get_config()["running"]:
            self.next_check_ts = time.time() + 5   # nach Neustart kurz warten, dann weiter
        while True:
            try:
                self._tick()
            except Exception as exc:  # Thread darf nie still sterben
                log.exception("Unerwarteter Fehler in der Überwachung")
                self._alert("crash", f"Interner Fehler in der Überwachung: {exc}")
                self.scanning = False
                self.next_check_ts = time.time() + 60
                time.sleep(5)

    def _tick(self):
        cfg = self.store.get_config()
        self._check_stale(cfg)
        now = time.time()
        if cfg["running"] and self.next_check_ts is not None and now >= self.next_check_ts:
            self._run_once(cfg)
            self._alert_clear("crash", "Überwachung läuft wieder normal.")
            cfg = self.store.get_config()
            delay = random.randint(cfg["interval_min"], cfg["interval_max"])
            if self.blocked_until and self.blocked_until > time.time():
                delay = max(delay, int(self.blocked_until - time.time()))
            self.next_check_ts = time.time() + delay if cfg["running"] else None
            return
        timeout = 30.0
        if cfg["running"] and self.next_check_ts is not None:
            timeout = max(0.5, min(timeout, self.next_check_ts - now))
        self._wake.wait(timeout)
        self._wake.clear()

    def _run_once(self, cfg):
        sources = active_sources(cfg)
        self.store.forget_sources([key for key, _, _ in sources])
        active_ids = {s["id"] for _, s, _ in sources}
        self.health = {k: v for k, v in self.health.items() if k in active_ids}
        for key in [k for k in self.alerts if ":" in k and k.split(":", 1)[1] not in active_ids]:
            self.alerts.pop(key)
        if not sources:
            self.last_check_ts = self.last_success_ts = time.time()
            self._alert_clear("stale")
            return

        self.scanning = True
        errors, new_items, any_success = {}, [], False
        try:
            for i, (key, search, url) in enumerate(sources):
                if i:
                    time.sleep(random.uniform(2, 5))   # nicht im Sekundentakt anfragen
                try:
                    page = self._fetch_with_retry(url)
                except scraper.ScrapeError as exc:
                    errors[search["name"]] = str(exc)
                    self._record_error(search, str(exc))
                    self._save_history("record_error", search, url, str(exc))
                    if exc.blocked:
                        self.blocked_until = time.time() + 15 * 60
                        log.warning("Blockiert bei %s – pausiere 15 Minuten", search["name"])
                        break
                    continue
                if self._record_page(search, page):
                    any_success = True
                self._save_history("record_page", search, url, page)
                for item in self.store.ingest(key, search, page.listings):
                    new_items.append((item, search))
        finally:
            self.scanning = False

        self.errors = errors
        if not errors:
            self.blocked_until = None
        self.last_check_ts = time.time()
        if any_success:
            self.last_success_ts = self.last_check_ts
            self._alert_clear("stale", "Suchläufe funktionieren wieder.")
        self.last_new_count = len(new_items)
        if new_items:
            log.info("%d neue Anzeige(n)", len(new_items))
            self._notify(new_items)

    def _save_history(self, method, *args):
        """Schreibt in den Verlauf. Ein Datenbankfehler darf die Überwachung nie stoppen."""
        if self.history is None:
            return
        try:
            getattr(self.history, method)(*args)
        except Exception:
            log.exception("Verlauf konnte nicht gespeichert werden")

    def _record_error(self, search, message):
        h = self.health.setdefault(search["id"], {"empty_runs": 0, "error_runs": 0})
        h.update(name=search["name"], state="error", last_error=message)
        h["error_runs"] += 1
        if h["error_runs"] >= config.WATCHDOG_ERROR_RUNS:
            self._alert(f"error:{search['id']}",
                        f"„{search['name']}“ schlägt seit {h['error_runs']} Läufen fehl: {message}")

    def _record_page(self, search, page):
        """Aktualisiert den Wächter-Zustand. True, wenn die Seite plausibel ausgewertet wurde."""
        sid, name = search["id"], search["name"]
        h = self.health.setdefault(sid, {"empty_runs": 0, "error_runs": 0})
        h.update(name=name, last_count=len(page.listings), error_runs=0, last_error="")
        self._alert_clear(f"error:{sid}", f"„{name}“ lässt sich wieder abrufen.")

        if page.suspicious:
            h["empty_runs"] += 1
            h["state"] = "suspicious"
            if h["empty_runs"] >= config.WATCHDOG_EMPTY_RUNS:
                self._alert(f"parser:{sid}",
                            f"„{name}“ liefert seit {h['empty_runs']} Läufen 0 Anzeigen, obwohl Kleinanzeigen "
                            "Treffer anzeigt. Vermutlich hat sich der Seitenaufbau geändert – Parser prüfen.")
            return False

        h["empty_runs"] = 0
        h["state"] = "empty" if not page.listings else "ok"
        h["last_ok_ts"] = time.time()
        self._alert_clear(f"parser:{sid}", f"„{name}“ liefert wieder Anzeigen.")

        if len(page.listings) >= 5 and page.missing_prices > 0.8:
            self._alert(f"fields:{sid}",
                        f"Bei „{name}“ fehlen die Preise fast aller Anzeigen – Parser prüfen.")
        elif page.listings:
            self._alert_clear(f"fields:{sid}", f"Bei „{name}“ werden Preise wieder erkannt.")
        return True

    @staticmethod
    def _fetch_with_retry(url, attempts=3):
        for attempt in range(attempts):
            try:
                return scraper.scrape(url)
            except scraper.ScrapeError as exc:
                if exc.blocked or attempt == attempts - 1:
                    raise
                time.sleep(2 * (attempt + 1))
        return scraper.PageResult()

    def _notify(self, items):
        cfg = self.store.get_config()
        if not cfg.get("telegram_enabled") or not telegram.is_configured():
            return
        wanted = [item for item, search in items if search.get("telegram", True) and passes_filters(item, search, cfg)]
        for listing in wanted[: config.MAX_TELEGRAM_PER_RUN]:
            telegram.notify_listing(listing)
        rest = len(wanted) - config.MAX_TELEGRAM_PER_RUN
        if rest > 0:
            try:
                telegram.send_message(f"… und {rest} weitere neue Anzeige(n) – siehe Weboberfläche.")
            except Exception as exc:
                log.error("Telegram-Versand fehlgeschlagen: %s", exc)
