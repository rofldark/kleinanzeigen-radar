import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import scraper, telegram
from app.monitor import Monitor, active_sources, passes_filters
from app.store import Store, clean_search

SEARCH = {"id": "s1", "type": "keyword", "query": "fahrrad", "name": "fahrrad"}


def ad(i, title="Fahrrad", price_value=10.0, posted="Heute, 12:00", **extra):
    base = {
        "id": str(i), "title": title, "url": f"https://www.kleinanzeigen.de/s-anzeige/x/{i}",
        "price": f"{price_value} €" if price_value is not None else "VB", "price_value": price_value,
        "location": "12345 Musterstadt", "posted": posted, "description": "",
        "image": "", "image_large": "", "badges": [], "is_request": False,
    }
    base.update(extra)
    return base


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_run_is_baseline_without_new(self):
        store = Store(self.dir)
        self.assertEqual(store.ingest("k", SEARCH, [ad(1), ad(2)]), [])
        self.assertEqual(len(store.listings), 2)
        self.assertEqual(store.unread, set())

    def test_second_run_reports_only_new(self):
        store = Store(self.dir)
        store.ingest("k", SEARCH, [ad(1), ad(2)])
        new = store.ingest("k", SEARCH, [ad(1), ad(2), ad(3)])
        self.assertEqual([n["id"] for n in new], ["3"])
        self.assertEqual(store.unread, {"3"})
        self.assertEqual(store.listings[0]["id"], "3")
        self.assertEqual(store.listings[0]["search_ids"], ["s1"])

    def test_old_ads_sliding_in_are_not_new(self):
        store = Store(self.dir)
        store.ingest("k", SEARCH, [ad(1)])
        new = store.ingest("k", SEARCH, [ad(1), ad(2, posted="01.09.2026"), ad(3, posted="Gestern, 09:00")])
        self.assertEqual([n["id"] for n in new], ["3"])
        self.assertIn("2", {l["id"] for l in store.listings})

    def test_listing_found_by_two_searches(self):
        store = Store(self.dir)
        other = dict(SEARCH, id="s2", name="nintendo")
        store.ingest("k1", SEARCH, [ad(1)])
        store.ingest("k2", other, [ad(1)])
        self.assertEqual(store.listings[0]["search_ids"], ["s1", "s2"])

    def test_state_survives_restart(self):
        store = Store(self.dir)
        store.ingest("k", SEARCH, [ad(1)])
        store.ingest("k", SEARCH, [ad(1), ad(2)])
        store.set_running(True)

        reloaded = Store(self.dir)
        self.assertTrue(reloaded.get_config()["running"])
        self.assertEqual(reloaded.unread, {"2"})
        self.assertEqual(reloaded.ingest("k", SEARCH, [ad(1), ad(2)]), [])

    def test_mark_single_listings_read(self):
        store = Store(self.dir)
        store.ingest("k", SEARCH, [ad(1)])
        store.ingest("k", SEARCH, [ad(1), ad(2), ad(3)])
        self.assertEqual(store.unread, {"2", "3"})
        rev = store.rev
        store.mark_read(["2", "99"])
        self.assertEqual(store.unread, {"3"})
        self.assertGreater(store.rev, rev)
        self.assertEqual(Store(self.dir).unread, {"3"})

    def test_clear_keeps_seen_ids(self):
        store = Store(self.dir)
        store.ingest("k", SEARCH, [ad(1)])
        store.clear_listings()
        self.assertEqual(store.listings, [])
        self.assertEqual(store.ingest("k", SEARCH, [ad(1)]), [])

    def test_config_validation(self):
        store = Store(self.dir)
        cfg = store.update_config({
            "searches": [
                {"query": " Fahrrad ", "price_min": 50, "price_max": 10, "must_contain": "Gravel, RENNRAD ,",
                 "location": {"id": "1234", "label": "12345 Musterstadt", "radius": 33}},
                {"type": "url", "url": "https://evil.example.com"},
                {"query": ""},
            ],
            "interval_min": 1, "interval_max": 5,
        })
        self.assertEqual(len(cfg["searches"]), 1)
        s = cfg["searches"][0]
        self.assertEqual((s["query"], s["name"]), ("Fahrrad", "Fahrrad"))
        self.assertEqual((s["price_min"], s["price_max"]), (10.0, 50.0))
        self.assertEqual(s["must_contain"], ["gravel", "rennrad"])
        self.assertEqual(s["location"], {"id": 1234, "label": "12345 Musterstadt", "radius": 30})
        self.assertEqual((cfg["interval_min"], cfg["interval_max"]), (30, 30))


class FilterTest(unittest.TestCase):
    CFG = {"blacklist": ["defekt"], "hide_requests": True}

    def test_global_filters(self):
        self.assertTrue(passes_filters(ad(1), None, self.CFG))
        self.assertFalse(passes_filters(ad(2, title="Fahrrad DEFEKT"), None, self.CFG))
        self.assertFalse(passes_filters(ad(3, is_request=True), None, self.CFG))

    def test_search_filters(self):
        s = clean_search({"query": "fahrrad", "price_min": 5, "price_max": 50, "show_no_price": False,
                          "must_contain": "gravel, rennrad", "exclude": "korb"})
        self.assertTrue(passes_filters(ad(1, title="Fahrrad Gravel"), s, self.CFG))
        self.assertFalse(passes_filters(ad(2, title="Fahrrad Hollandrad"), s, self.CFG))
        self.assertFalse(passes_filters(ad(3, title="Fahrrad Gravel Korb"), s, self.CFG))
        self.assertFalse(passes_filters(ad(4, title="Fahrrad Gravel", price_value=None), s, self.CFG))
        self.assertFalse(passes_filters(ad(5, title="Fahrrad Gravel", price_value=80.0), s, self.CFG))

    def test_active_sources(self):
        cfg = {"searches": [
            clean_search({"id": "a", "query": "Fahrrad", "location": {"id": 100, "label": "Musterregion"}, "price_max": 50}),
            clean_search({"id": "b", "query": "aus", "enabled": False}),
            clean_search({"id": "c", "type": "url", "name": "Spiele", "url": "https://www.kleinanzeigen.de/s-videospiele/c227"}),
        ]}
        sources = active_sources(cfg)
        self.assertEqual([s[1]["id"] for s in sources], ["a", "c"])
        self.assertEqual(sources[0][2], "https://www.kleinanzeigen.de/s-musterregion/preis::50/fahrrad/k0l100")


class WatchdogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name))
        self.store.update_config({"searches": [SEARCH]})
        self.monitor = Monitor(self.store)
        self.sent = []
        patcher = mock.patch.object(Monitor, "_send_system", side_effect=self.sent.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, page=None, error=None):
        with mock.patch.object(Monitor, "_fetch_with_retry",
                               side_effect=error or (lambda url: page)):
            self.monitor._run_once(self.store.get_config())

    def test_parser_warning_after_repeated_empty_pages(self):
        broken = scraper.PageResult(cards=0, no_results=False)
        self._run(broken)
        self.assertEqual(self.monitor.alerts, {})
        self._run(broken)
        self.assertEqual(len(self.monitor.alerts), 1)
        self.assertEqual(len(self.sent), 1)
        self._run(broken)
        self.assertEqual(len(self.sent), 1)          # nur einmal melden
        self._run(scraper.PageResult(listings=[ad(1)], cards=1))
        self.assertEqual(self.monitor.alerts, {})
        self.assertIn("wieder", self.sent[-1])       # Entwarnung

    def test_new_listing_in_tests_never_reaches_telegram(self):
        self._run(scraper.PageResult(listings=[ad(1)], cards=1))   # Baseline
        with mock.patch.object(telegram, "send_photo") as photo, mock.patch.object(telegram, "send_message") as msg:
            self._run(scraper.PageResult(listings=[ad(1), ad(2)], cards=2))   # ad(2) ist neu
        self.assertEqual(len(self.store.unread), 1)
        self.assertFalse(telegram.is_configured())
        photo.assert_not_called()
        msg.assert_not_called()

    def test_real_empty_result_is_fine(self):
        for _ in range(3):
            self._run(scraper.PageResult(no_results=True))
        self.assertEqual(self.monitor.alerts, {})
        self.assertIsNotNone(self.monitor.last_success_ts)

    def test_repeated_errors_warn(self):
        for _ in range(3):
            self._run(error=scraper.ScrapeError("HTTP 500"))
        self.assertEqual(len(self.monitor.alerts), 1)
        self.assertIn("HTTP 500", self.sent[0])

    def test_stale_detection(self):
        self.store.set_running(True)
        self.monitor.started_ts -= 3 * 3600
        self.monitor._check_stale(self.store.get_config())
        self.assertIn("stale", self.monitor.alerts)
        self.assertFalse(self.monitor.healthy())


if __name__ == "__main__":
    unittest.main()
