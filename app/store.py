"""Persistenter Zustand: Einstellungen, gefundene Anzeigen und bereits gesehene IDs.

Alles liegt in einer JSON-Datei (`DATA_DIR/state.json`), die atomar geschrieben wird.
So überlebt der Monitor Neustarts, ohne bekannte Anzeigen erneut als „neu“ zu melden.

Eine Suche (`searches`) ist entweder ein Suchbegriff (type "keyword") oder eine beliebige
Kleinanzeigen-URL (type "url", z. B. eine Kategorie) und hat ihre eigenen Filter.
"""

import json
import os
import threading
import time
import uuid
from pathlib import Path

from . import config, scraper

DEFAULT_CONFIG = {
    "searches": [],
    "interval_min": 120,
    "interval_max": 300,
    "blacklist": [],         # gilt für alle Suchen (Titelwörter, klein geschrieben)
    "hide_requests": True,   # „Gesuch“-Anzeigen ausblenden
    "telegram_enabled": True,
    "highlight_zip": [],     # PLZ-Anfänge, deren Orte farbig hervorgehoben werden, z. B. ["66", "67"]
    "search_sets": [],       # gespeicherte Such-Sets: [{"label": str, "searches": [...]}]
    "running": False,        # Überwachung aktiv – wird nach Neustart fortgesetzt
}

SEARCH_DEFAULTS = {
    "type": "keyword",
    "query": "",
    "url": "",
    "name": "",
    "enabled": True,
    "location": None,        # {"id": int, "label": str, "radius": int} – nur bei Suchbegriffen
    "price_min": None,
    "price_max": None,
    "show_no_price": True,   # Anzeigen ohne Preis (VB, k. A.) zeigen
    "must_contain": [],      # Titel muss mindestens eines dieser Wörter enthalten
    "exclude": [],           # Titel darf keines dieser Wörter enthalten
    "telegram": True,
}


def _text(v, limit=200):
    return str(v if v is not None else "").strip()[:limit]


def _words(value):
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        return []
    out = []
    for w in value:
        w = _text(w, 60).lower()
        if w and w not in out:
            out.append(w)
    return out[:30]


def _price(value):
    if value in (None, ""):
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def _location(value):
    if not isinstance(value, dict):
        return None
    try:
        loc_id = int(value.get("id"))
    except (TypeError, ValueError):
        return None
    if loc_id <= 0:
        return None
    try:
        radius = int(value.get("radius") or 0)
    except (TypeError, ValueError):
        radius = 0
    radius = min(scraper.RADIUS_OPTIONS, key=lambda r: abs(r - radius))
    return {"id": loc_id, "label": _text(value.get("label"), 80) or str(loc_id), "radius": radius}


def clean_search(data):
    """Normalisiert eine Suche; None, wenn sie unbrauchbar ist."""
    if not isinstance(data, dict):
        return None
    s = dict(SEARCH_DEFAULTS)
    s["id"] = _text(data.get("id"), 40) or uuid.uuid4().hex[:10]
    s["type"] = "url" if data.get("type") == "url" else "keyword"
    s["enabled"] = bool(data.get("enabled", True))
    s["price_min"] = _price(data.get("price_min"))
    s["price_max"] = _price(data.get("price_max"))
    if s["price_min"] is not None and s["price_max"] is not None and s["price_min"] > s["price_max"]:
        s["price_min"], s["price_max"] = s["price_max"], s["price_min"]
    s["show_no_price"] = bool(data.get("show_no_price", True))
    s["must_contain"] = _words(data.get("must_contain"))
    s["exclude"] = _words(data.get("exclude"))
    s["telegram"] = bool(data.get("telegram", True))
    if s["type"] == "keyword":
        s["query"] = _text(data.get("query"), 100)
        if not s["query"]:
            return None
        s["name"] = _text(data.get("name"), 100) or s["query"]
        s["location"] = _location(data.get("location"))
    else:
        s["url"] = _text(data.get("url"), 1000)
        if not scraper.is_kleinanzeigen_url(s["url"]):
            return None
        s["name"] = _text(data.get("name"), 100) or "Kategorie"
    return s


def _clean_searches(items):
    out, ids = [], set()
    for item in items if isinstance(items, list) else []:
        s = clean_search(item)
        if s and s["id"] not in ids:
            ids.add(s["id"])
            out.append(s)
    return out[:100]


def clean_config(data, current):
    """Übernimmt nur bekannte Felder mit plausiblem Typ."""
    out = dict(current)
    if "searches" in data:
        out["searches"] = _clean_searches(data["searches"])
    if "blacklist" in data:
        out["blacklist"] = _words(data["blacklist"])
    if "highlight_zip" in data:
        out["highlight_zip"] = [w for w in _words(data["highlight_zip"]) if w.isdigit() and len(w) <= 5]
    if isinstance(data.get("search_sets"), list):
        sets = []
        for f in data["search_sets"][:50]:
            if isinstance(f, dict) and _text(f.get("label")):
                sets.append({"label": _text(f["label"]), "searches": _clean_searches(f.get("searches"))})
        out["search_sets"] = sets
    for key in ("interval_min", "interval_max"):
        if key in data:
            try:
                out[key] = max(config.MIN_INTERVAL, int(data[key]))
            except (TypeError, ValueError):
                pass
    out["interval_max"] = max(out["interval_min"], out["interval_max"])
    for key in ("hide_requests", "telegram_enabled"):
        if key in data:
            out[key] = bool(data[key])
    return out


class Store:
    def __init__(self, data_dir=None):
        self.dir = Path(data_dir or config.DATA_DIR)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "state.json"
        self.lock = threading.RLock()
        self.config = dict(DEFAULT_CONFIG)
        self.listings = []        # neueste zuerst
        self.seen_ids = []        # Reihenfolge = Alter, für Kürzung
        self._seen_set = set()
        self.baselined = set()    # Quellen, deren Erstbestand schon erfasst ist
        self.unread = set()       # als NEU markierte Anzeigen
        self.rev = 0              # erhöht sich bei jeder sichtbaren Änderung an Anzeigen
        self._load()

    # ── Laden / Speichern ──────────────────────────
    def _load(self):
        if self.path.is_file():
            try:
                d = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                d = {}
                self.path.replace(self.path.with_suffix(f".broken-{int(time.time())}.json"))
            raw = d.get("config", {})
            self.config = clean_config(raw, DEFAULT_CONFIG)
            self.config["running"] = bool(raw.get("running", False))
            self.listings = d.get("listings", [])
            self.seen_ids = d.get("seen_ids", [])
            self.baselined = set(d.get("baselined", []))
            self.unread = set(d.get("unread", []))
        self._seen_set = set(self.seen_ids)
        self.unread &= {l["id"] for l in self.listings}

    def save(self):
        with self.lock:
            data = {
                "config": self.config,
                "listings": self.listings,
                "seen_ids": self.seen_ids,
                "baselined": sorted(self.baselined),
                "unread": sorted(self.unread),
            }
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)

    # ── Einstellungen ──────────────────────────────
    def get_config(self):
        with self.lock:
            return json.loads(json.dumps(self.config))

    def update_config(self, data):
        with self.lock:
            running = self.config["running"]
            self.config = clean_config(data, self.config)
            self.config["running"] = running
            self.save()
            return self.get_config()

    def set_running(self, running):
        with self.lock:
            self.config["running"] = bool(running)
            self.save()

    # ── Anzeigen ───────────────────────────────────
    def ingest(self, source_key, search, found):
        """Übernimmt Treffer einer Suche und gibt die wirklich neuen Anzeigen zurück.

        - Beim ersten Durchlauf einer Quelle gilt der gesamte Bestand als bekannt (keine Meldungen).
        - Anzeigen, die älter als MAX_AGE_DAYS inseriert wurden, gelten nie als neu. Sie tauchen auf,
          wenn neuere Anzeigen verschwinden und ältere auf die erste Ergebnisseite nachrutschen.
        """
        now = time.time()
        sid = search["id"]
        with self.lock:
            first_run = source_key not in self.baselined
            by_id = {l["id"]: l for l in self.listings}
            new, changed = [], first_run
            for item in found:
                lid = item["id"]
                existing = by_id.get(lid)
                if existing:
                    for key in ("title", "price", "price_value", "image", "image_large", "badges", "is_request", "posted"):
                        if existing.get(key) != item[key]:
                            existing[key] = item[key]
                            changed = True
                    if sid not in existing.setdefault("search_ids", []):
                        existing["search_ids"].append(sid)
                        changed = True
                    continue
                entry = dict(item, source=search["name"], search_ids=[sid], first_seen=now)
                if lid not in self._seen_set:
                    self._seen_set.add(lid)
                    self.seen_ids.append(lid)
                    too_old = scraper.posted_age_days(item.get("posted")) > config.MAX_AGE_DAYS
                    if not first_run and not too_old:
                        new.append(entry)
                        self.unread.add(lid)
                by_id[lid] = entry
                self.listings.append(entry)
                changed = True
            self.baselined.add(source_key)
            if changed:
                self.listings.sort(key=lambda l: l.get("first_seen", 0), reverse=True)
                self._trim()
                self.rev += 1
            self.save()
            return new

    def _trim(self):
        if len(self.listings) > config.MAX_LISTINGS:
            self.listings = self.listings[: config.MAX_LISTINGS]
            self.unread &= {l["id"] for l in self.listings}
        if len(self.seen_ids) > config.MAX_SEEN_IDS:
            drop = len(self.seen_ids) - config.MAX_SEEN_IDS
            self._seen_set.difference_update(self.seen_ids[:drop])
            self.seen_ids = self.seen_ids[drop:]

    def forget_sources(self, active_keys):
        """Quellen, die nicht mehr konfiguriert sind, beim nächsten Hinzufügen neu einlesen."""
        with self.lock:
            if self.baselined - set(active_keys):
                self.baselined &= set(active_keys)
                self.save()

    def mark_read(self, ids):
        """Markiert einzelne Anzeigen als gelesen (z. B. beim Öffnen in der Vorschau)."""
        with self.lock:
            hit = self.unread & {str(i) for i in ids}
            if hit:
                self.unread -= hit
                self.rev += 1
                self.save()

    def mark_all_read(self):
        with self.lock:
            if self.unread:
                self.unread.clear()
                self.rev += 1
                self.save()

    def clear_listings(self):
        """Leert die Liste. Gesehene IDs bleiben, damit alte Anzeigen nicht erneut gemeldet werden."""
        with self.lock:
            self.listings = []
            self.unread.clear()
            self.rev += 1
            self.save()

    def snapshot(self):
        with self.lock:
            return {
                "rev": self.rev,
                "listings": json.loads(json.dumps(self.listings)),
                "unread": sorted(self.unread),
            }
