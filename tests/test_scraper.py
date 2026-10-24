import re
import unittest
from datetime import date
from pathlib import Path

from app import scraper

FIXTURE = Path(__file__).parent / "fixtures" / "search_results.html"
RADIUS_FIXTURE = Path(__file__).parent / "fixtures" / "radius_other_places.html"


class ParsePageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = FIXTURE.read_text(encoding="utf-8")
        cls.result = scraper.parse_page(cls.page)
        cls.listings = cls.result.listings

    def test_top_ads_are_skipped(self):
        # Fixture: 2 TOP-Anzeigen + 5 normale Anzeigen
        self.assertEqual(self.result.cards, 7)
        self.assertEqual(self.result.top_ads, 2)
        self.assertEqual(len(self.listings), 5)
        self.assertNotIn("1000000000", {l["id"] for l in self.listings})
        self.assertFalse(self.result.suspicious)

    def test_fields(self):
        first = self.listings[0]
        self.assertEqual(first["id"], "1000002222")
        self.assertTrue(first["title"].startswith("Kinderfahrrad 20 Zoll"))
        self.assertEqual(first["price"], "25 €")
        self.assertEqual(first["price_value"], 25.0)
        self.assertEqual(first["location"], "25838 Neudorf")
        self.assertEqual(first["posted"], "Heute, 21:57")
        self.assertTrue(first["url"].startswith("https://www.kleinanzeigen.de/s-anzeige/"))
        self.assertIn("rule=$_35.AUTO", first["image"])
        self.assertIn("rule=$_59.AUTO", first["image_large"])
        self.assertIn("Versand möglich", first["badges"])
        self.assertFalse(first["is_request"])

    def test_negotiable_price(self):
        vb = [l for l in self.listings if l["price"] == "16 € VB"]
        self.assertEqual(len(vb), 1)
        self.assertEqual(vb[0]["price_value"], 16.0)

    def test_json_ld_fallback_for_title_and_image(self):
        # Titel-Markup und <img> entfernen – JSON-LD muss einspringen
        broken = re.sub(r"<h3\b.*?</h3>", "", self.page, flags=re.S)
        broken = re.sub(r"<img\b[^>]*>", "", broken)
        listings = scraper.parse_page(broken).listings
        self.assertEqual(len(listings), 5)
        self.assertTrue(listings[0]["title"].startswith("Kinderfahrrad 20 Zoll"))
        self.assertTrue(listings[0]["image"].startswith("https://img.kleinanzeigen.de/"))


class WatchdogSignalsTest(unittest.TestCase):
    def test_no_results_page_is_not_suspicious(self):
        page = "<html><body><h1>Es wurden keine Ergebnisse für „xyz“ gefunden.</h1></body></html>"
        result = scraper.parse_page(page)
        self.assertTrue(result.no_results)
        self.assertFalse(result.suspicious)

    def test_changed_layout_is_suspicious(self):
        page = "<html><body><div class='new-layout'><div data-id='1'>Anzeige</div></div></body></html>"
        self.assertTrue(scraper.parse_page(page).suspicious)

    def test_only_top_ads_is_not_suspicious(self):
        page = FIXTURE.read_text(encoding="utf-8")
        cards = re.findall(r'<li data-clickable="card".*?</li>', page, re.S)
        result = scraper.parse_page("<ul>" + "".join(cards[:2]) + "</ul>")
        self.assertEqual(result.listings, [])
        self.assertFalse(result.suspicious)

    def test_results_from_other_places_are_ignored(self):
        # Umkreissuche mit wenigen Treffern: Kleinanzeigen hängt bundesweite Anzeigen als zweite Liste an
        page = FIXTURE.read_text(encoding="utf-8")
        cards = re.findall(r'<li data-clickable="card".*?</li>', page, re.S)
        html = ('<ul id="srchrslt-adtable">' + cards[2] + '</ul>'
                '<h2>Weitere Ergebnisse in anderen Orten</h2>'
                '<ul id="srchrslt-adtable-altads">' + "".join(cards[3:6]) + '</ul>')
        result = scraper.parse_page(html)
        self.assertEqual(len(result.listings), 1)
        self.assertEqual(result.other_places, 3)

    def test_only_other_places_is_not_suspicious(self):
        page = FIXTURE.read_text(encoding="utf-8")
        cards = re.findall(r'<li data-clickable="card".*?</li>', page, re.S)
        result = scraper.parse_page('<ul id="srchrslt-adtable"></ul><ul id="srchrslt-adtable-altads">' + cards[3] + '</ul>')
        self.assertEqual(result.listings, [])
        self.assertFalse(result.suspicious)

    def test_real_radius_page(self):
        # Gekürzte Umkreissuche (anonymisiert): 6 eigene Treffer (PLZ 12…) + 10 aus anderen Orten
        result = scraper.parse_page(RADIUS_FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(len(result.listings), 6)
        self.assertTrue(all(l["location"].startswith("12") for l in result.listings))
        self.assertEqual(result.other_places, 10)

    def test_missing_prices(self):
        page = re.sub(r'(<p\b[^>]*class="[^"]*)\btext-title3\b', r"\1gone", FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(scraper.parse_page(page).missing_prices, 1.0)


class HelpersTest(unittest.TestCase):
    def test_parse_price(self):
        self.assertEqual(scraper.parse_price("1.250 € VB"), 1250.0)
        self.assertEqual(scraper.parse_price("12,50 €"), 12.5)
        self.assertEqual(scraper.parse_price("Zu verschenken"), 0.0)
        self.assertIsNone(scraper.parse_price("VB"))
        self.assertIsNone(scraper.parse_price(""))

    def test_search_url(self):
        self.assertEqual(scraper.search_url("Lego Technic"),
                         "https://www.kleinanzeigen.de/s-lego-technic/k0")
        self.assertEqual(scraper.search_url("fahrrad", {"id": 100, "label": "Musterregion", "radius": 0}),
                         "https://www.kleinanzeigen.de/s-musterregion/fahrrad/k0l100")
        self.assertEqual(scraper.search_url("fahrrad", {"id": 1234, "label": "12345 Musterstadt", "radius": 30}, 10, 50),
                         "https://www.kleinanzeigen.de/s-12345-musterstadt/preis:10:50/fahrrad/k0l1234r30")
        self.assertEqual(scraper.search_url("fahrrad", None, None, 30.5),
                         "https://www.kleinanzeigen.de/s-preis::31/fahrrad/k0")

    def test_posted_age_days(self):
        today = date(2026, 9, 23)
        self.assertEqual(scraper.posted_age_days("Heute, 12:00", today), 0)
        self.assertEqual(scraper.posted_age_days("Gestern, 08:15", today), 1)
        self.assertEqual(scraper.posted_age_days("18.09.2026", today), 5)
        self.assertEqual(scraper.posted_age_days("", today), 0)

    def test_is_kleinanzeigen_url(self):
        self.assertTrue(scraper.is_kleinanzeigen_url("https://www.kleinanzeigen.de/s-videospiele/c227"))
        self.assertFalse(scraper.is_kleinanzeigen_url("https://kleinanzeigen.de.evil.com/x"))
        self.assertFalse(scraper.is_kleinanzeigen_url("javascript://www.kleinanzeigen.de/x"))
        self.assertFalse(scraper.is_kleinanzeigen_url("kein link"))


if __name__ == "__main__":
    unittest.main()
