"""Verlauf aller Suchergebnisse in einer SQLite-Datenbank (`DATA_DIR/history.db`).

Unabhängig von Filtern, Baseline und Listenkürzung wird hier alles gesammelt, was die
Suchen liefern – als Grundlage für spätere Auswertungen (Preisentwicklung, Laufzeiten …).

Tabellen:
- searches          jede Suche mit ihren Einstellungen (Stand der letzten Abfrage)
- listings          jede Anzeige einmal, mit dem zuletzt gesehenen Stand
- listing_searches  welche Suche eine Anzeige wann gefunden hat (Trennung nach Suchbegriff)
- listing_changes   Änderungen an Preis, Titel, Beschreibung … einer Anzeige
- runs              jeder Abruf einer Suche mit Trefferzahl bzw. Fehler

Zeitstempel sind Unix-Sekunden (`datetime(ts, 'unixepoch', 'localtime')` in SQLite).
"""

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS searches (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    type          TEXT NOT NULL,
    query         TEXT,
    url           TEXT,
    request_url   TEXT,
    location_id   INTEGER,
    location      TEXT,
    radius_km     INTEGER,
    price_min     REAL,
    price_max     REAL,
    first_seen    INTEGER NOT NULL,
    last_seen     INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS listings (
    id            TEXT PRIMARY KEY,
    title         TEXT,
    url           TEXT,
    price         TEXT,
    price_value   REAL,
    location      TEXT,
    posted        TEXT,
    description   TEXT,
    image         TEXT,
    image_large   TEXT,
    badges        TEXT,
    is_request    INTEGER,
    first_seen    INTEGER NOT NULL,
    last_seen     INTEGER NOT NULL,
    seen_count    INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS listing_searches (
    listing_id    TEXT NOT NULL REFERENCES listings(id),
    search_id     TEXT NOT NULL REFERENCES searches(id),
    first_seen    INTEGER NOT NULL,
    last_seen     INTEGER NOT NULL,
    seen_count    INTEGER NOT NULL DEFAULT 1,
    first_rank    INTEGER,
    PRIMARY KEY (listing_id, search_id)
);
CREATE INDEX IF NOT EXISTS idx_listing_searches_search ON listing_searches(search_id, first_seen);
CREATE TABLE IF NOT EXISTS listing_changes (
    listing_id    TEXT NOT NULL REFERENCES listings(id),
    ts            INTEGER NOT NULL,
    field         TEXT NOT NULL,
    old_value     TEXT,
    new_value     TEXT
);
CREATE INDEX IF NOT EXISTS idx_listing_changes_listing ON listing_changes(listing_id, ts);
CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    search_id     TEXT NOT NULL,
    ts            INTEGER NOT NULL,
    request_url   TEXT,
    ok            INTEGER NOT NULL,
    error         TEXT,
    listings      INTEGER,
    cards         INTEGER,
    top_ads       INTEGER,
    no_results    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_runs_search ON runs(search_id, ts);
"""

# Felder, deren Änderung in listing_changes protokolliert wird
TRACKED_FIELDS = ("title", "price", "price_value", "location", "posted", "description",
                  "image", "badges", "is_request")


def _db_value(key, value):
    if key == "badges":
        return json.dumps(value or [], ensure_ascii=False)
    if key == "is_request":
        return int(bool(value))
    return value


class History:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self):
        with self.lock:
            self.db.close()

    def is_empty(self):
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 0

    # ── Schreiben ──────────────────────────────────
    def _upsert_search(self, search, request_url, ts):
        loc = search.get("location") or {}
        self.db.execute(
            """INSERT INTO searches (id, name, type, query, url, request_url, location_id, location,
                                     radius_km, price_min, price_max, first_seen, last_seen)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name, type=excluded.type, query=excluded.query,
                   url=excluded.url, request_url=excluded.request_url, location_id=excluded.location_id,
                   location=excluded.location, radius_km=excluded.radius_km, price_min=excluded.price_min,
                   price_max=excluded.price_max, last_seen=excluded.last_seen""",
            (search["id"], search.get("name", ""), search.get("type", "keyword"), search.get("query") or None,
             search.get("url") or None, request_url, loc.get("id"), loc.get("label"), loc.get("radius"),
             search.get("price_min"), search.get("price_max"), ts, ts))

    def _upsert_listing(self, item, search_id, rank, ts, first_seen=None):
        first_seen = first_seen or ts
        row = self.db.execute(
            f"SELECT {', '.join(TRACKED_FIELDS)} FROM listings WHERE id = ?", (item["id"],)).fetchone()
        values = {k: _db_value(k, item.get(k)) for k in TRACKED_FIELDS}
        if row is None:
            self.db.execute(
                """INSERT INTO listings (id, url, image_large, first_seen, last_seen, seen_count, """
                + ", ".join(TRACKED_FIELDS) + ") VALUES (?,?,?,?,?,1," + ",".join("?" * len(TRACKED_FIELDS)) + ")",
                (item["id"], item.get("url"), item.get("image_large"), first_seen, ts,
                 *(values[k] for k in TRACKED_FIELDS)))
        else:
            old = dict(zip(TRACKED_FIELDS, row))
            for key in TRACKED_FIELDS:
                if old[key] != values[key]:
                    self.db.execute(
                        "INSERT INTO listing_changes (listing_id, ts, field, old_value, new_value) VALUES (?,?,?,?,?)",
                        (item["id"], ts, key, None if old[key] is None else str(old[key]),
                         None if values[key] is None else str(values[key])))
            self.db.execute(
                "UPDATE listings SET url=?, image_large=?, last_seen=?, seen_count=seen_count+1, "
                + ", ".join(f"{k}=?" for k in TRACKED_FIELDS) + " WHERE id=?",
                (item.get("url"), item.get("image_large"), ts, *(values[k] for k in TRACKED_FIELDS), item["id"]))
        self.db.execute(
            """INSERT INTO listing_searches (listing_id, search_id, first_seen, last_seen, seen_count, first_rank)
               VALUES (?,?,?,?,1,?)
               ON CONFLICT(listing_id, search_id) DO UPDATE SET last_seen=excluded.last_seen,
                   seen_count=seen_count+1""",
            (item["id"], search_id, first_seen, ts, rank))

    def record_page(self, search, request_url, page, ts=None):
        """Speichert einen erfolgreichen Abruf samt aller gefundenen Anzeigen (ungefiltert)."""
        ts = int(ts or time.time())
        with self.lock, self.db:
            self._upsert_search(search, request_url, ts)
            for rank, item in enumerate(page.listings, 1):
                self._upsert_listing(item, search["id"], rank, ts)
            self.db.execute(
                """INSERT INTO runs (search_id, ts, request_url, ok, listings, cards, top_ads, no_results)
                   VALUES (?,?,?,1,?,?,?,?)""",
                (search["id"], ts, request_url, len(page.listings), page.cards, page.top_ads, int(page.no_results)))

    def record_error(self, search, request_url, message, ts=None):
        ts = int(ts or time.time())
        with self.lock, self.db:
            self._upsert_search(search, request_url, ts)
            self.db.execute("INSERT INTO runs (search_id, ts, request_url, ok, error) VALUES (?,?,?,0,?)",
                            (search["id"], ts, request_url, message))

    # ── Lesen (Datenbank-Tab der Oberfläche) ────────
    def _rows(self, sql, args=()):
        cur = self.db.execute(sql, args)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def size_bytes(self):
        total = 0
        for suffix in ("", "-wal"):
            try:
                total += self.path.with_name(self.path.name + suffix).stat().st_size
            except OSError:
                pass
        return total

    def stats(self, now=None):
        """Kennzahlen gesamt und je Suche."""
        day_ago = int(now or time.time()) - 86400
        with self.lock:
            totals = self._rows("""
                SELECT (SELECT COUNT(*) FROM listings) AS listings,
                       (SELECT COUNT(*) FROM searches) AS searches,
                       (SELECT COUNT(*) FROM runs) AS runs,
                       (SELECT COUNT(*) FROM runs WHERE ok = 0) AS run_errors,
                       (SELECT COUNT(*) FROM listing_changes) AS changes,
                       (SELECT COUNT(*) FROM listing_changes WHERE field = 'price_value') AS price_changes,
                       (SELECT MIN(first_seen) FROM listings) AS since,
                       (SELECT MAX(ts) FROM runs) AS last_run""")[0]
            searches = self._rows("""
                WITH last_ok AS (SELECT search_id, MAX(ts) AS ts FROM runs WHERE ok = 1 GROUP BY search_id)
                SELECT s.id, s.name, s.type, s.query, s.url, s.location, s.radius_km, s.price_min, s.price_max,
                       s.first_seen,
                       COUNT(ls.listing_id) AS listings,
                       SUM(ls.first_seen > ?) AS new_24h,
                       SUM(ls.last_seen >= lo.ts) AS current,
                       MIN(l.price_value) AS price_min_seen,
                       AVG(l.price_value) AS price_avg,
                       MAX(l.price_value) AS price_max_seen,
                       (SELECT MAX(ts) FROM runs r WHERE r.search_id = s.id) AS last_run,
                       (SELECT ok FROM runs r WHERE r.search_id = s.id ORDER BY ts DESC, id DESC LIMIT 1) AS last_run_ok,
                       (SELECT COUNT(*) FROM runs r WHERE r.search_id = s.id) AS runs
                FROM searches s
                LEFT JOIN listing_searches ls ON ls.search_id = s.id
                LEFT JOIN listings l ON l.id = ls.listing_id
                LEFT JOIN last_ok lo ON lo.search_id = s.id
                GROUP BY s.id ORDER BY s.first_seen""", (day_ago,))
        totals["size_bytes"] = self.size_bytes()
        return {"totals": totals, "searches": searches}

    LISTING_SORTS = {
        "new": "first_seen DESC",
        "old": "first_seen ASC",
        "seen": "last_seen DESC",
        "price_asc": "price_value IS NULL, price_value ASC",
        "price_desc": "price_value IS NULL, price_value DESC",
        "duration": "(last_seen - first_seen) DESC",
        "changes": "changes DESC, first_seen DESC",
    }

    def query_listings(self, search_id=None, text=None, status=None, sort="new", page=1, per_page=50):
        """Anzeigen mit Filter, Sortierung und Seiten. status: None | "current" | "gone"."""
        where, args = [], []
        if search_id:
            where.append("ls.search_id = ?")
            args.append(search_id)
        if text:
            where.append("(l.title LIKE ? OR l.description LIKE ? OR l.location LIKE ?)")
            args += [f"%{text}%"] * 3
        having = {"current": "HAVING is_current = 1", "gone": "HAVING is_current = 0"}.get(status, "")
        order = self.LISTING_SORTS.get(sort, self.LISTING_SORTS["new"])
        base = f"""
            WITH last_ok AS (SELECT search_id, MAX(ts) AS ts FROM runs WHERE ok = 1 GROUP BY search_id)
            SELECT l.id, l.title, l.url, l.price, l.price_value, l.location, l.posted, l.image, l.is_request,
                   l.badges, l.seen_count,
                   MIN(ls.first_seen) AS first_seen, MAX(ls.last_seen) AS last_seen,
                   GROUP_CONCAT(ls.search_id) AS search_ids,
                   MAX(ls.last_seen >= COALESCE(lo.ts, 0)) AS is_current,
                   (SELECT COUNT(*) FROM listing_changes c WHERE c.listing_id = l.id) AS changes,
                   (SELECT COUNT(*) FROM listing_changes c WHERE c.listing_id = l.id AND c.field = 'price') AS price_changes,
                   (SELECT COUNT(*) FROM listing_changes c WHERE c.listing_id = l.id
                        AND c.field NOT IN ('price', 'price_value')) AS other_changes
            FROM listings l
            JOIN listing_searches ls ON ls.listing_id = l.id
            LEFT JOIN last_ok lo ON lo.search_id = ls.search_id
            {"WHERE " + " AND ".join(where) if where else ""}
            GROUP BY l.id {having}"""
        page = max(1, int(page))
        with self.lock:
            total = self.db.execute(f"SELECT COUNT(*) FROM ({base})", args).fetchone()[0]
            rows = self._rows(f"{base} ORDER BY {order} LIMIT ? OFFSET ?", [*args, per_page, (page - 1) * per_page])
        for r in rows:
            r["search_ids"] = (r["search_ids"] or "").split(",") if r["search_ids"] else []
            r["badges"] = json.loads(r["badges"] or "[]")
            r["is_current"] = bool(r["is_current"])
        return {"total": total, "page": page, "per_page": per_page, "items": rows}

    def listing_detail(self, listing_id):
        with self.lock:
            rows = self._rows("SELECT * FROM listings WHERE id = ?", (listing_id,))
            if not rows:
                return None
            listing = rows[0]
            listing["badges"] = json.loads(listing["badges"] or "[]")
            listing["searches"] = self._rows("""
                SELECT ls.search_id, s.name, ls.first_seen, ls.last_seen, ls.seen_count, ls.first_rank
                FROM listing_searches ls LEFT JOIN searches s ON s.id = ls.search_id
                WHERE ls.listing_id = ? ORDER BY ls.first_seen""", (listing_id,))
            listing["changes"] = self._rows(
                "SELECT ts, field, old_value, new_value FROM listing_changes WHERE listing_id = ? ORDER BY ts, rowid",
                (listing_id,))
        return listing

    def query_changes(self, search_id=None, field=None, page=1, per_page=50):
        where, args = [], []
        if search_id:
            where.append("c.listing_id IN (SELECT listing_id FROM listing_searches WHERE search_id = ?)")
            args.append(search_id)
        if field:
            where.append("c.field = ?")
            args.append(field)
        else:
            where.append("c.field != 'price_value'")   # doppelt zu „price“, nur für Auswertungen
        cond = "WHERE " + " AND ".join(where) if where else ""
        page = max(1, int(page))
        with self.lock:
            total = self.db.execute(f"SELECT COUNT(*) FROM listing_changes c {cond}", args).fetchone()[0]
            rows = self._rows(f"""
                SELECT c.listing_id, c.ts, c.field, c.old_value, c.new_value, l.title, l.url, l.price
                FROM listing_changes c JOIN listings l ON l.id = c.listing_id
                {cond} ORDER BY c.ts DESC, c.rowid DESC LIMIT ? OFFSET ?""",
                [*args, per_page, (page - 1) * per_page])
        return {"total": total, "page": page, "per_page": per_page, "items": rows}

    def query_runs(self, search_id=None, errors_only=False, page=1, per_page=50):
        where, args = [], []
        if search_id:
            where.append("r.search_id = ?")
            args.append(search_id)
        if errors_only:
            where.append("r.ok = 0")
        cond = "WHERE " + " AND ".join(where) if where else ""
        page = max(1, int(page))
        with self.lock:
            total = self.db.execute(f"SELECT COUNT(*) FROM runs r {cond}", args).fetchone()[0]
            rows = self._rows(f"""
                SELECT r.id, r.search_id, s.name, r.ts, r.request_url, r.ok, r.error, r.listings, r.cards,
                       r.top_ads, r.no_results
                FROM runs r LEFT JOIN searches s ON s.id = r.search_id
                {cond} ORDER BY r.ts DESC, r.id DESC LIMIT ? OFFSET ?""",
                [*args, per_page, (page - 1) * per_page])
        return {"total": total, "page": page, "per_page": per_page, "items": rows}

    def export_bytes(self):
        """Konsistente Kopie der gesamten Datenbank (für den Download)."""
        with self.lock:
            return self.db.serialize()

    def import_listings(self, searches, listings):
        """Einmalige Übernahme der bereits gespeicherten Trefferliste (state.json)."""
        by_id = {s["id"]: s for s in searches}
        with self.lock, self.db:
            for listing in sorted(listings, key=lambda l: l.get("first_seen", 0)):
                ts = int(listing.get("first_seen") or time.time())
                for sid in listing.get("search_ids") or []:
                    if sid in by_id:
                        self._upsert_search(by_id[sid], None, ts)
                        self._upsert_listing(listing, sid, None, ts, first_seen=ts)
