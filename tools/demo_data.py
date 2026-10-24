"""Erzeugt erfundene Demodaten (Suche „iphone 18 pro“) für die Screenshots in der README.

Aufruf (aus dem Projektordner):
    python tools/demo_data.py demo-data
    DATA_DIR=demo-data python main.py      # Überwachung nicht starten, sonst wird echt gesucht

Es wird nichts bei Kleinanzeigen abgerufen. Alle Anzeigen sind erfunden, die Bilder
sind selbst gezeichnete SVG-Grafiken (data-URI).
"""
import base64
import sys
import time
from pathlib import Path
from unittest import mock

project, data_dir = Path(__file__).resolve().parent.parent, Path(sys.argv[1])
sys.path.insert(0, str(project))

from app import scraper  # noqa: E402
from app.history import History  # noqa: E402
from app.store import Store  # noqa: E402

COLORS = {  # Gehäuse, Kamera-Insel, Hintergrund oben, Hintergrund unten
    "Silber": ("#d9dadc", "#c4c6c9", "#efe9e1", "#d8cfc3"),
    "Schwarz": ("#3a3b3e", "#2b2c2e", "#e4e7ea", "#c9cfd6"),
    "Dunkelblau": ("#2f3e57", "#26334a", "#f1ece6", "#ddd2c6"),
    "Kupfer": ("#c98b62", "#b77a52", "#e9eef0", "#cfd8dc"),
    "Weiß": ("#f2f2f0", "#e2e2df", "#d9e1e6", "#b9c6cf"),
}


def phone_svg(color, variant):
    body, island, bg1, bg2 = COLORS[color]
    rot = (-8, 6, -3, 10, 0)[variant % 5]
    box = '<rect x="250" y="250" width="150" height="95" rx="10" fill="#f7f7f5" stroke="#d0d0cc"/>' if variant % 3 == 1 else ""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 300">
<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{bg1}"/><stop offset="1" stop-color="{bg2}"/></linearGradient>
<linearGradient id="b" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#ffffff" stop-opacity=".35"/><stop offset=".5" stop-color="#ffffff" stop-opacity="0"/></linearGradient></defs>
<rect width="400" height="300" fill="url(#g)"/>{box}
<g transform="rotate({rot} 200 150)">
<rect x="143" y="28" width="114" height="244" rx="22" fill="#00000022" transform="translate(6 6)"/>
<rect x="143" y="28" width="114" height="244" rx="22" fill="{body}" stroke="#00000030"/>
<rect x="143" y="28" width="114" height="244" rx="22" fill="url(#b)"/>
<rect x="153" y="38" width="62" height="62" rx="14" fill="{island}" stroke="#00000025"/>
<circle cx="170" cy="55" r="10" fill="#1d1f22" stroke="#6b6e73" stroke-width="2"/>
<circle cx="170" cy="83" r="10" fill="#1d1f22" stroke="#6b6e73" stroke-width="2"/>
<circle cx="198" cy="69" r="10" fill="#1d1f22" stroke="#6b6e73" stroke-width="2"/>
<circle cx="203" cy="47" r="3" fill="#f3e6c4"/>
<circle cx="200" cy="160" r="9" fill="none" stroke="#00000022" stroke-width="2"/>
</g></svg>'''
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


# (id, Titel, Farbe, Preis, Ort, Datum, Badges, Beschreibung)
OLD = [
    ("1000310001", "iPhone 18 Pro 256 GB Silber – wie neu, mit Rechnung", "Silber", 1049, "20095 Hamburg", 150, ["Versand möglich"],
     "Verkaufe mein iPhone 18 Pro in Silber, gekauft im September. Immer mit Hülle und Schutzglas benutzt, keine Kratzer. Originalkarton, Kabel und Rechnung liegen bei."),
    ("1000310002", "iPhone 18 Pro 512 GB Dunkelblau", "Dunkelblau", 1190, "10115 Berlin", 175, ["Versand möglich", "Direkt kaufen"],
     "Neuwertig, Akku 100 %. Wurde nur drei Wochen genutzt, da ich zu einem anderen Modell gewechselt bin."),
    ("1000310003", "iPhone 18 Pro 256 GB Schwarz, OVP versiegelt", "Schwarz", 1120, "80331 München", 230, ["Versand möglich"],
     "Originalverpackt und versiegelt, Geschenk doppelt bekommen. Abholung oder versicherter Versand."),
    ("1000310004", "iPhone 18 Pro Kupfer 1 TB", "Kupfer", 1390, "50667 Köln", 620, [],
     "Top Zustand, Displayschutz seit Tag 1. Nur Abholung, gerne mit Barzahlung vor Ort."),
    ("1000310005", "Tausche iPhone 18 Pro gegen iPhone 18 Pro Max", "Weiß", None, "22041 Hamburg", 770, [],
     "Mein iPhone 18 Pro (256 GB, Weiß) gegen ein Pro Max, gerne mit Zuzahlung meinerseits."),
    ("1000310006", "iPhone 18 Pro 256 GB Weiß mit AppleCare+", "Weiß", 1099, "60311 Frankfurt am Main", 905, ["Versand möglich"],
     "AppleCare+ bis 2028, wird übertragen. Kleine Gebrauchsspur am Rahmen, sonst einwandfrei."),
    ("1000310007", "iPhone 18 Pro 128 GB Schwarz – Display gesprungen", "Schwarz", 520, "04109 Leipzig", 1180, ["Versand möglich"],
     "Display hat einen Sprung in der oberen Ecke, Touch funktioniert vollständig. Face ID geht."),
]
NEW = [
    ("1000310008", "iPhone 18 Pro 256 GB Dunkelblau, Akku 100 %", "Dunkelblau", 1079, "21073 Hamburg", 9, ["Versand möglich"],
     "Wenige Wochen alt, kein einziger Kratzer. Mit Originalkarton und zwei Hüllen. Privatverkauf, keine Garantie über die Apple-Garantie hinaus."),
    ("1000310009", "iPhone 18 Pro 512 GB Silber + MagSafe-Hülle", "Silber", 1240, "70173 Stuttgart", 21, ["Versand möglich", "Direkt kaufen"],
     "Neuwertig, Rechnung vom Händler vorhanden. MagSafe-Hülle und Panzerglas gibt es dazu."),
    ("1000310010", "Suche iPhone 18 Pro, gerne mit Displayschaden", "Schwarz", None, "30159 Hannover", 44, [],
     "Suche ein iPhone 18 Pro, auch mit Schäden. Bitte alles anbieten."),
    ("1000310011", "iPhone 18 Pro 256 GB Kupfer – neu & unbenutzt", "Kupfer", 1150, "22767 Hamburg", 71, ["Versand möglich"],
     "Vertragsverlängerung, Gerät nie eingeschaltet. Folie noch drauf."),
]


def posted_text(minutes):
    from datetime import datetime, timedelta
    now = datetime.now()
    t = now - timedelta(minutes=minutes)
    day = "Heute" if t.date() == now.date() else "Gestern" if (now.date() - t.date()).days == 1 else t.strftime("%d.%m.%Y")
    return f"{day}, {t:%H:%M}"


def item(row, n):
    lid, title, color, price, loc, minutes, badges, desc = row
    posted = posted_text(minutes)
    return {
        "id": lid, "title": title,
        "url": f"https://www.kleinanzeigen.de/s-anzeige/demo/{lid}",
        "price": "VB" if price is None else f"{price:,} € VB".replace(",", "."),
        "price_value": None if price is None else float(price),
        "location": loc, "posted": posted, "description": desc,
        "image": phone_svg(color, n), "image_large": phone_svg(color, n),
        "badges": badges, "is_request": title.startswith("Suche"),
    }


def main():
    data_dir.mkdir(parents=True, exist_ok=True)
    store = Store(data_dir)
    cfg = store.update_config({
        "searches": [{"id": "demo1", "query": "iphone 18 pro", "price_min": 400, "price_max": 1500,
                      "exclude": ["defekt", "attrappe"], "telegram": True}],
        "highlight_zip": ["20", "21", "22"],
        "hide_requests": False,
        "interval_min": 120, "interval_max": 300,
    })
    search = cfg["searches"][0]
    url = scraper.search_url(search["query"], search["location"], search["price_min"], search["price_max"])
    key = f"{search['id']}|{url}"
    history = History(data_dir / "history.db")
    now = time.time()
    old = [item(r, i) for i, r in enumerate(OLD)]
    new = [item(r, i + 7) for i, r in enumerate(NEW)]
    # Frühere Abrufe: Bestand, dann eine Preissenkung
    for k, ts in enumerate([now - 140 * 60, now - 100 * 60, now - 60 * 60]):
        page_items = [dict(x) for x in old]
        if k == 0:
            page_items[0].update(price="1.099 € VB", price_value=1099.0)
            page_items[3].update(price="1.450 €", price_value=1450.0)
        if k == 1:
            page_items[3].update(price="1.420 €", price_value=1420.0)
        history.record_page(search, url, scraper.PageResult(listings=page_items, cards=len(page_items)), ts=ts)
    history.record_error(search, url, "HTTP 503 (Kleinanzeigen nicht erreichbar)", ts=now - 80 * 60)
    with mock.patch("app.store.time.time", return_value=now - 140 * 60):
        store.ingest(key, search, old)
    # Letzter Abruf: vier neue Anzeigen
    with mock.patch("app.store.time.time", return_value=now - 5 * 60):
        store.ingest(key, search, new + old)
    history.record_page(search, url, scraper.PageResult(listings=new + old, cards=len(new + old)), ts=now - 5 * 60)
    print("Anzeigen:", len(store.listings), "ungelesen:", len(store.unread))


main()
