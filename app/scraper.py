"""Abruf und Auswertung von Kleinanzeigen-Suchergebnisseiten.

Kleinanzeigen liefert die Treffer als Karten:
    <li data-clickable="card"> [SVG-Abzeichen bei TOP-Anzeigen] <article data-adid=… data-href=…> … </article></li>
Ort und Datum stehen als <span> im Kopf der Karte, der Titel in <h3><a>, der Preis in
<p class="… text-title3 …">, Zusatzinfos wie „Gesuch“ oder „Versand möglich“ als <span data-dhl-promotion>.
Jede Karte enthält außerdem JSON-LD (Titel, Beschreibung, Bild) – das dient als Rückfallquelle.

Such-URLs:  /s-[ort-slug/][preis:MIN:MAX/]begriff/k0[l<orts-id>[r<radius-km>]]
Der Orts-Slug ist beliebig, entscheidend ist die Orts-ID (siehe location_suggestions).
"""

import html
import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime

BASE_URL = "https://www.kleinanzeigen.de"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9",
    "Referer": BASE_URL + "/",
}

RADIUS_OPTIONS = [0, 5, 10, 20, 30, 50, 100, 150, 200]


class ScrapeError(Exception):
    """Abruf fehlgeschlagen. `blocked` ist True, wenn Kleinanzeigen den Zugriff abweist."""

    def __init__(self, message, blocked=False):
        super().__init__(message)
        self.blocked = blocked


@dataclass
class PageResult:
    """Anzeigen einer Ergebnisseite plus Kennzahlen für den Parser-Wächter."""
    listings: list = field(default_factory=list)
    cards: int = 0            # gefundene Karten insgesamt (inkl. TOP-Anzeigen)
    top_ads: int = 0
    no_results: bool = False  # Kleinanzeigen meldet ausdrücklich „keine Ergebnisse“
    other_places: int = 0     # ignorierte Karten aus „Weitere Ergebnisse in anderen Orten“

    @property
    def suspicious(self):
        """True, wenn die Seite nach Treffern aussieht, der Parser aber nichts herausbekommt."""
        if self.listings or self.no_results:
            return False
        # Umkreissuche ohne eigene Treffer: Kleinanzeigen zeigt nur Anzeigen aus anderen Orten
        if self.other_places:
            return False
        # Nur TOP-Anzeigen auf der Seite ist ein normales (seltenes) Ergebnis
        return not (self.cards and self.cards == self.top_ads)

    @property
    def missing_prices(self):
        """Anteil der Anzeigen ohne erkannten Preistext (0–1)."""
        if not self.listings:
            return 0.0
        return sum(1 for l in self.listings if not l["price"]) / len(self.listings)


# ── URLs ─────────────────────────────────────────────

def _slug(text):
    text = text.strip().lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "ort"


def search_url(keyword, location=None, price_min=None, price_max=None):
    """Baut die Such-URL für einen Begriff mit optionalem Ort/Umkreis und Preisbereich."""
    segments = []
    if location and location.get("id"):
        segments.append(_slug(location.get("label", "")))
    if price_min is not None or price_max is not None:
        lo = "" if price_min is None else str(int(math.floor(price_min)))
        hi = "" if price_max is None else str(int(math.ceil(price_max)))
        segments.append(f"preis:{lo}:{hi}")
    segments.append(urllib.parse.quote(re.sub(r"\s+", "-", keyword.strip().lower())))
    suffix = "k0"
    if location and location.get("id"):
        suffix += f"l{int(location['id'])}"
        if location.get("radius"):
            suffix += f"r{int(location['radius'])}"
    return f"{BASE_URL}/s-{'/'.join(segments)}/{suffix}"


def is_kleinanzeigen_url(url):
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    host = parsed.hostname or ""
    return parsed.scheme in ("http", "https") and (host == "kleinanzeigen.de" or host.endswith(".kleinanzeigen.de"))


# ── Abruf ────────────────────────────────────────────

def fetch(url, timeout=20, headers=None):
    req = urllib.request.Request(url, headers=headers or HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            raise ScrapeError(f"Kleinanzeigen blockiert den Zugriff (HTTP {e.code})", blocked=True) from e
        raise ScrapeError(f"HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise ScrapeError(f"Netzwerkfehler: {e.reason}") from e
    except TimeoutError as e:
        raise ScrapeError("Zeitüberschreitung") from e


def location_suggestions(query):
    """Ortsvorschläge von Kleinanzeigen: [{"id": 1234, "label": "12345 Musterstadt"}, …]."""
    query = query.strip()
    if len(query) < 2:
        return []
    url = f"{BASE_URL}/s-ort-empfehlungen.json?query={urllib.parse.quote(query)}"
    body = fetch(url, timeout=10, headers=dict(HEADERS, Accept="application/json",
                                                 **{"X-Requested-With": "XMLHttpRequest"}))
    try:
        data = json.loads(body)
    except ValueError as e:
        raise ScrapeError("Ortsvorschläge nicht lesbar") from e
    out = []
    for key, label in data.items():
        try:
            loc_id = int(str(key).lstrip("_"))
        except ValueError:
            continue
        if loc_id:  # _0 = „Deutschland“
            out.append({"id": loc_id, "label": str(label)})
    return out


# ── Auswertung ───────────────────────────────────────

_CARD_RE = re.compile(r'<li\b[^>]*data-clickable="card"[^>]*>(.*?)</li>', re.S)
_ADID_RE = re.compile(r'<article\b[^>]*\bdata-adid="(\d+)"')
_HREF_RE = re.compile(r'<article\b[^>]*\bdata-href="([^"]+)"')
_LINK_RE = re.compile(r'href="(/s-anzeige/[^"]+)"')
_TITLE_RE = re.compile(r"<h3\b[^>]*>\s*<a\b[^>]*>(.*?)</a>", re.S)
_DESC_RE = re.compile(r"</h3>\s*<p\b[^>]*>(.*?)</p>", re.S)
_PRICE_RE = re.compile(r'<p\b[^>]*class="[^"]*\btext-title3\b[^"]*"[^>]*>(.*?)</p>', re.S)
_SPAN_RE = re.compile(r"<span>([^<]*)</span>")
_BADGE_RE = re.compile(r"<span\b[^>]*data-dhl-promotion[^>]*>(.*?)</span>", re.S)
_IMG_RE = re.compile(r'<img\b[^>]*\bsrc="([^"]+)"')
_SRCSET_RE = re.compile(r'<img\b[^>]*\bsrcset="([^"\s]+)')
_LDJSON_RE = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
_DATE_RE = re.compile(r"^(Heute|Gestern|\d{2}\.\d{2}\.\d{4})")
_OTHER_PLACES_RE = re.compile(r'<ul\b[^>]*\bid="srchrslt-adtable-altads"')
_NO_RESULTS_RE = re.compile(r"Es wurden (?:leider )?keine Ergebnisse")
_TAG_RE = re.compile(r"<[^>]+>")
_SVG_RE = re.compile(r"<svg\b.*?</svg>", re.S)


def _text(fragment):
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", _SVG_RE.sub("", fragment)))).strip()


def _image_rule(url, rule):
    """Tauscht die Bildgröße in einer Kleinanzeigen-Bild-URL (rule=$_2.AUTO → $_59.AUTO …)."""
    if not url:
        return ""
    url = html.unescape(url)
    return re.sub(r"rule=\$_\d+\.(AUTO|JPG|jpg)", f"rule=$_{rule}.AUTO", url)


def parse_price(text):
    """'1.250 € VB' → 1250.0, 'Zu verschenken' → 0.0, 'VB' → None."""
    if not text:
        return None
    if re.search(r"verschenk", text, re.I):
        return 0.0
    m = re.search(r"\d[\d.]*(?:,\d+)?", text)
    if not m:
        return None
    try:
        return float(m.group(0).replace(".", "").replace(",", "."))
    except ValueError:
        return None


def posted_age_days(posted, today=None):
    """Alter einer Anzeige in Tagen aus 'Heute, 12:00' / 'Gestern, …' / '20.09.2026'. Unbekannt → 0."""
    today = today or date.today()
    if not posted or posted.startswith("Heute"):
        return 0
    if posted.startswith("Gestern"):
        return 1
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", posted)
    if not m:
        return 0
    try:
        return (today - datetime.strptime(m.group(0), "%d.%m.%Y").date()).days
    except ValueError:
        return 0


def _ld_json(card):
    m = _LDJSON_RE.search(card)
    if not m:
        return {}
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _parse_card(card):
    adid = _ADID_RE.search(card)
    if not adid:
        return None
    ld = _ld_json(card)

    title_m = _TITLE_RE.search(card)
    title = _text(title_m.group(1)) if title_m else str(ld.get("title", "")).strip()
    if not title:
        return None

    href = _HREF_RE.search(card) or _LINK_RE.search(card)
    url = html.unescape(href.group(1)) if href else f"/s-anzeige/{adid.group(1)}"
    if url.startswith("/"):
        url = BASE_URL + url

    head = card[: card.find("<h3")] if "<h3" in card else card
    spans = [html.unescape(s).strip() for s in _SPAN_RE.findall(head)]
    location = spans[0] if spans else ""
    posted = next((s for s in spans[1:] if _DATE_RE.match(s)), "")

    price_m = _PRICE_RE.search(card)
    price = _text(price_m.group(1)) if price_m else ""
    desc_m = _DESC_RE.search(card)
    description = _text(desc_m.group(1)) if desc_m else str(ld.get("description", "")).strip()
    badges = [b for b in (_text(x) for x in _BADGE_RE.findall(card)) if b]

    img = _SRCSET_RE.search(card) or _IMG_RE.search(card)
    image = img.group(1) if img else str(ld.get("contentUrl", ""))
    image = _image_rule(image, 35) if image.startswith("http") else ""

    return {
        "id": adid.group(1),
        "title": title,
        "url": url,
        "price": price,
        "price_value": parse_price(price),
        "location": location,
        "posted": posted,
        "description": description[:300],
        "image": image,
        "image_large": _image_rule(image, 59),
        "badges": badges,
        "is_request": "Gesuch" in badges,
    }


def parse_page(page):
    """Wertet eine Ergebnisseite aus. TOP-Anzeigen (bezahlte Werbung) werden übersprungen.

    Bei Umkreissuchen mit wenigen Treffern hängt Kleinanzeigen eine zweite Liste
    „Weitere Ergebnisse in anderen Orten“ (`srchrslt-adtable-altads`) mit Anzeigen aus ganz
    Deutschland an. Die gehören nicht zur Suche und werden nur gezählt.
    """
    result = PageResult(no_results=bool(_NO_RESULTS_RE.search(page)))
    cut = _OTHER_PLACES_RE.search(page)
    if cut:
        result.other_places = len(_CARD_RE.findall(page[cut.start():]))
        page = page[:cut.start()]
    for m in _CARD_RE.finditer(page):
        card = m.group(1)
        article_pos = card.find("<article")
        if article_pos < 0:
            continue
        result.cards += 1
        # TOP-Anzeigen tragen ein SVG-Abzeichen vor dem <article>
        if "<svg" in card[:article_pos] or "is-topad" in card[:article_pos]:
            result.top_ads += 1
            continue
        listing = _parse_card(card)
        if listing:
            result.listings.append(listing)
    return result


def parse_results(page):
    return parse_page(page).listings


def scrape(url):
    return parse_page(fetch(url))
