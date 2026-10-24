import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import scraper
from app.history import History
from app.monitor import Monitor
from app.store import Store
from tests.test_store import ad

BIKE = {"id": "s1", "type": "keyword", "query": "fahrrad", "name": "fahrrad", "enabled": True,
        "location": {"id": 100, "label": "Musterregion", "radius": 0}, "price_min": None, "price_max": 50.0}
OSMO = {"id": "s2", "type": "keyword", "query": "osmo", "name": "osmo", "enabled": True, "location": None}


def page(*items, **kw):
    return scraper.PageResult(listings=list(items), cards=len(items), **kw)


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.h = History(self.dir / "history.db")

    def tearDown(self):
        self.h.close()
        self.tmp.cleanup()

    def q(self, sql, *args):
        return self.h.db.execute(sql, args).fetchall()

    def test_stores_listings_per_search(self):
        self.h.record_page(BIKE, "u1", page(ad(1, "Rennrad"), ad(2, "Klapprad")), ts=100)
        self.h.record_page(OSMO, "u2", page(ad(2, "Klapprad"), ad(3, "Osmo")), ts=110)
        self.assertEqual(self.q("SELECT COUNT(*) FROM listings")[0][0], 3)
        self.assertEqual(self.q("SELECT listing_id, first_rank FROM listing_searches WHERE search_id='s1' ORDER BY listing_id"),
                         [("1", 1), ("2", 2)])
        self.assertEqual(self.q("SELECT search_id FROM listing_searches WHERE listing_id='2' ORDER BY search_id"),
                         [("s1",), ("s2",)])
        self.assertEqual(self.q("SELECT location, location_id, price_max FROM searches WHERE id='s1'"),
                         [("Musterregion", 100, 50.0)])

    def test_repeat_updates_last_seen_and_logs_changes(self):
        self.h.record_page(BIKE, "u1", page(ad(1, price_value=40.0)), ts=100)
        self.h.record_page(BIKE, "u1", page(ad(1, price_value=40.0)), ts=200)
        self.h.record_page(BIKE, "u1", page(ad(1, price_value=30.0)), ts=300)
        self.assertEqual(self.q("SELECT first_seen, last_seen, seen_count, price_value FROM listings"),
                         [(100, 300, 3, 30.0)])
        self.assertEqual(self.q("SELECT last_seen, seen_count FROM listing_searches"), [(300, 3)])
        changes = self.q("SELECT field, old_value, new_value FROM listing_changes ORDER BY field")
        self.assertEqual(changes, [("price", "40.0 €", "30.0 €"), ("price_value", "40.0", "30.0")])

    def test_runs_include_errors(self):
        self.h.record_page(BIKE, "u1", page(ad(1)), ts=100)
        self.h.record_error(BIKE, "u1", "HTTP 500", ts=200)
        self.assertEqual(self.q("SELECT ts, ok, error, listings FROM runs ORDER BY ts"),
                         [(100, 1, None, 1), (200, 0, "HTTP 500", None)])

    def test_import_existing_listings(self):
        listings = [dict(ad(1), search_ids=["s1"], first_seen=50.5), dict(ad(2), search_ids=["unknown"], first_seen=60)]
        self.h.import_listings([BIKE], listings)
        self.assertEqual(self.q("SELECT id, first_seen FROM listings"), [("1", 50)])
        self.assertFalse(self.h.is_empty())

    def test_stats_per_search(self):
        self.h.record_page(BIKE, "u1", page(ad(1, price_value=10.0), ad(2, price_value=30.0)), ts=1000)
        self.h.record_page(BIKE, "u1", page(ad(1, price_value=10.0)), ts=2000)
        self.h.record_error(OSMO, "u2", "HTTP 500", ts=2000)
        st = self.h.stats(now=2000)
        self.assertEqual((st["totals"]["listings"], st["totals"]["runs"], st["totals"]["run_errors"]), (2, 3, 1))
        bike = next(s for s in st["searches"] if s["id"] == "s1")
        self.assertEqual((bike["listings"], bike["current"], bike["price_avg"], bike["last_run_ok"]), (2, 1, 20.0, 1))
        osmo = next(s for s in st["searches"] if s["id"] == "s2")
        self.assertEqual((osmo["listings"], osmo["last_run_ok"]), (0, 0))

    def test_query_listings_filters_and_status(self):
        self.h.record_page(BIKE, "u1", page(ad(1, "Rennrad", 10.0), ad(2, "Klapprad", 30.0)), ts=1000)
        self.h.record_page(OSMO, "u2", page(ad(2, "Klapprad", 30.0), ad(3, "Osmo", 300.0)), ts=1100)
        self.h.record_page(BIKE, "u1", page(ad(1, "Rennrad", 8.0)), ts=2000)

        all_ = self.h.query_listings()
        self.assertEqual(all_["total"], 3)
        klapprad = next(i for i in all_["items"] if i["id"] == "2")
        self.assertEqual(sorted(klapprad["search_ids"]), ["s1", "s2"])
        self.assertTrue(klapprad["is_current"])   # bei „osmo“ noch in den Treffern

        bike = self.h.query_listings(search_id="s1")
        self.assertEqual({i["id"] for i in bike["items"]}, {"1", "2"})
        gone = self.h.query_listings(search_id="s1", status="gone")
        self.assertEqual([i["id"] for i in gone["items"]], ["2"])
        self.assertEqual([i["id"] for i in self.h.query_listings(text="klapp")["items"]], ["2"])
        by_price = self.h.query_listings(sort="price_desc")["items"]
        self.assertEqual([i["id"] for i in by_price], ["3", "2", "1"])
        he_man = next(i for i in all_["items"] if i["id"] == "1")
        self.assertEqual((he_man["price_changes"], he_man["other_changes"]), (1, 0))

    def test_paging(self):
        self.h.record_page(BIKE, "u1", page(*[ad(i) for i in range(1, 8)]), ts=1000)
        p2 = self.h.query_listings(page=2, per_page=5)
        self.assertEqual((p2["total"], len(p2["items"])), (7, 2))

    def test_detail_changes_runs_and_export(self):
        self.h.record_page(BIKE, "u1", page(ad(1, "Rennrad", 10.0)), ts=1000)
        self.h.record_page(BIKE, "u1", page(ad(1, "Rennrad Carbon", 10.0)), ts=2000)
        self.h.record_error(BIKE, "u1", "HTTP 500", ts=3000)
        detail = self.h.listing_detail("1")
        self.assertEqual([c["field"] for c in detail["changes"]], ["title"])
        self.assertEqual(detail["searches"][0]["name"], "fahrrad")
        self.assertIsNone(self.h.listing_detail("nope"))
        self.assertEqual(self.h.query_changes(search_id="s1")["total"], 1)
        self.assertEqual(self.h.query_changes(field="price")["total"], 0)
        self.assertEqual(self.h.query_runs(errors_only=True)["items"][0]["error"], "HTTP 500")
        self.assertEqual(self.h.query_runs(search_id="s1")["total"], 3)
        self.assertTrue(self.h.export_bytes().startswith(b"SQLite format 3"))

    def test_monitor_records_everything_unfiltered(self):
        store = Store(self.dir)
        store.update_config({"searches": [BIKE], "blacklist": ["klapprad"]})
        monitor = Monitor(store, self.h)
        with mock.patch.object(Monitor, "_fetch_with_retry", return_value=page(ad(1, "Rennrad"), ad(2, "Klapprad", price_value=999.0))):
            monitor._run_once(store.get_config())
        self.assertEqual(self.q("SELECT COUNT(*) FROM listings")[0][0], 2)
        self.assertEqual(self.q("SELECT ok, listings FROM runs"), [(1, 2)])

    def test_history_failure_does_not_stop_monitor(self):
        store = Store(self.dir)
        store.update_config({"searches": [BIKE]})
        broken = mock.Mock()
        broken.record_page.side_effect = RuntimeError("disk full")
        monitor = Monitor(store, broken)
        with mock.patch.object(Monitor, "_fetch_with_retry", return_value=page(ad(1))):
            monitor._run_once(store.get_config())
        self.assertEqual(len(store.listings), 1)


if __name__ == "__main__":
    unittest.main()
