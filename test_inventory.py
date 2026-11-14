"""
Tests for the database layer and the HTTP API.

Run:   python3 -m unittest -v

Each test gets its own in-memory database, so tests never touch
inventory.db and never depend on each other.
"""
import json
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date

import app
import db

LAPTOP = {"tag": "lt-0100", "type": "laptop", "model": "Dell Latitude 5540", "serial": "ABC123",
          "status": "in_stock", "location": "IT storage", "warranty_end": "2027-01-31"}


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")

    def tearDown(self):
        self.conn.close()

    def test_create_tidies_the_data_and_logs_history(self):
        asset = db.create_asset(self.conn, LAPTOP)
        self.assertEqual(asset["tag"], "LT-0100")                   # tags are stored in capitals
        self.assertEqual(db.asset_history(self.conn, asset["id"])[0]["event"], "Added to inventory")

    def test_validation_messages(self):
        with self.assertRaises(db.ValidationError) as caught:
            db.create_asset(self.conn, {"tag": "", "type": "toaster", "model": "", "warranty_end": "31/01/2027"})
        self.assertEqual(len(caught.exception.errors), 4)

    def test_assigned_asset_needs_a_person(self):
        with self.assertRaises(db.ValidationError):
            db.create_asset(self.conn, dict(LAPTOP, status="assigned"))
        asset = db.create_asset(self.conn, dict(LAPTOP, status="assigned", assigned_to="Maria"))
        self.assertEqual(asset["assigned_to"], "Maria")

    def test_duplicate_tag_is_refused(self):
        db.create_asset(self.conn, LAPTOP)
        with self.assertRaises(db.ValidationError):
            db.create_asset(self.conn, LAPTOP)

    def test_update_records_what_changed(self):
        asset = db.create_asset(self.conn, LAPTOP)
        db.update_asset(self.conn, asset["id"], dict(LAPTOP, status="assigned", assigned_to="Nikos", location="Athens HQ"))
        events = [h["event"] for h in db.asset_history(self.conn, asset["id"])]
        self.assertIn("Assigned to Nikos", events)
        self.assertIn("Moved to Athens HQ", events)
        # Returning it to stock clears the owner.
        back = db.update_asset(self.conn, asset["id"], dict(LAPTOP, status="in_stock", assigned_to="Nikos"))
        self.assertEqual(back["assigned_to"], "")
        self.assertIsNone(db.update_asset(self.conn, 999, LAPTOP))

    def test_search_and_filters(self):
        db.seed(self.conn, today=date(2026, 1, 1))
        self.assertEqual(len(db.list_assets(self.conn)), 9)
        self.assertEqual(len(db.list_assets(self.conn, status="assigned")), 4)
        self.assertEqual(len(db.list_assets(self.conn, type_="laptop", status="assigned")), 2)
        self.assertEqual([a["tag"] for a in db.list_assets(self.conn, query="maria")], ["LT-0001", "MN-0001"])

    def test_search_text_cannot_inject_sql(self):
        db.seed(self.conn)
        self.assertEqual(db.list_assets(self.conn, query="' OR 1=1 --"), [])
        self.assertEqual(len(db.list_assets(self.conn)), 9)         # nothing was damaged

    def test_summary_counts_warranties(self):
        today = date(2026, 1, 1)
        db.seed(self.conn, today=today)
        s = db.summary(self.conn, today=today)
        self.assertEqual(s["total"], 9)
        self.assertEqual(s["by_status"], {"in_stock": 3, "assigned": 4, "repair": 1, "retired": 1})
        self.assertEqual(s["warranty_expiring"], 2)                 # LT-0002 (30 days) and PH-0001 (45 days)
        self.assertEqual(s["warranty_expired"], 1)                  # LT-0003; the retired laptop is ignored

    def test_delete_removes_the_history_too(self):
        asset = db.create_asset(self.conn, LAPTOP)
        self.assertTrue(db.delete_asset(self.conn, asset["id"]))
        self.assertFalse(db.delete_asset(self.conn, asset["id"]))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM history").fetchone()[0], 0)


class ApiTests(unittest.TestCase):
    """Starts the real server on a free port and talks to it over HTTP."""

    def setUp(self):
        self.conn = db.connect(":memory:")
        self.server = app.make_server(self.conn, port=0)            # port 0 = let the system pick a free one
        self.server.quiet = True
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.conn.close()

    def call(self, method, path, body=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        request = urllib.request.Request(self.base + path, data=data, method=method)
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_full_life_of_an_asset(self):
        status, created = self.call("POST", "/api/assets", LAPTOP)
        self.assertEqual(status, 201)
        url = "/api/assets/%d" % created["id"]

        status, updated = self.call("PUT", url, dict(LAPTOP, status="assigned", assigned_to="Eleni"))
        self.assertEqual((status, updated["assigned_to"]), (200, "Eleni"))

        status, listed = self.call("GET", "/api/assets?q=eleni")
        self.assertEqual((status, len(listed)), (200, 1))

        status, history = self.call("GET", url + "/history")
        self.assertEqual(history[0]["event"], "Assigned to Eleni")

        self.assertEqual(self.call("GET", "/api/summary")[1]["by_status"]["assigned"], 1)
        self.assertEqual(self.call("DELETE", url)[0], 200)
        self.assertEqual(self.call("DELETE", url)[0], 404)

    def test_bad_requests_get_clear_errors(self):
        status, body = self.call("POST", "/api/assets", {"tag": "X"})
        self.assertEqual(status, 400)
        self.assertTrue(body["errors"])
        self.assertEqual(self.call("POST", "/api/assets", raw=b"not json")[0], 400)
        self.assertEqual(self.call("POST", "/api/assets", raw=b"x" * 20000)[0], 400)
        self.assertEqual(self.call("PUT", "/api/assets/999", LAPTOP)[0], 404)
        self.assertEqual(self.call("GET", "/api/assets/999/history")[0], 404)
        self.assertEqual(self.call("GET", "/api/unknown")[0], 404)

    def test_only_the_listed_files_are_served(self):
        with urllib.request.urlopen(self.base + "/") as response:
            self.assertIn(b"IT Asset Inventory", response.read())
        self.assertEqual(self.call("GET", "/../app.py")[0], 404)
        self.assertEqual(self.call("GET", "/schema.sql")[0], 404)


if __name__ == "__main__":
    unittest.main()
