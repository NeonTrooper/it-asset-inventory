"""
db.py — everything that talks to the database.

The web server (app.py) never writes SQL itself; it calls the functions
here. Every query uses "?" placeholders, so values typed by a user are
always sent to SQLite as data and can never change the SQL (no SQL injection).
"""
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

SCHEMA = Path(__file__).with_name("schema.sql")

TYPES = ("laptop", "desktop", "monitor", "phone", "printer", "network", "other")
STATUSES = ("in_stock", "assigned", "repair", "retired")
FIELDS = ("tag", "type", "model", "serial", "status", "assigned_to", "location", "warranty_end")


class ValidationError(Exception):
    """Raised when the data for an asset is not acceptable. Holds a list of messages."""

    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = errors


def connect(path="inventory.db"):
    """Open the database (creating the tables on first use)."""
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row              # rows behave like dictionaries
    conn.execute("PRAGMA foreign_keys = ON")    # needed for ON DELETE CASCADE
    conn.executescript(SCHEMA.read_text())
    return conn


def _now():
    return datetime.now().isoformat(timespec="seconds")


def clean(data):
    """Check and tidy the data for one asset. Returns a new dict or raises ValidationError."""
    errors = []
    asset = {field: str(data.get(field) or "").strip() for field in FIELDS}
    asset["tag"] = asset["tag"].upper()
    asset["status"] = asset["status"] or "in_stock"

    if not asset["tag"]:
        errors.append("Asset tag is required.")
    elif len(asset["tag"]) > 20:
        errors.append("Asset tag is too long (20 characters maximum).")
    if asset["type"] not in TYPES:
        errors.append("Type must be one of: " + ", ".join(TYPES) + ".")
    if not asset["model"]:
        errors.append("Model is required.")
    if asset["status"] not in STATUSES:
        errors.append("Status must be one of: " + ", ".join(STATUSES) + ".")
    if asset["status"] == "assigned" and not asset["assigned_to"]:
        errors.append("An assigned asset needs the name of the person it is assigned to.")
    if asset["status"] != "assigned":
        asset["assigned_to"] = ""               # only assigned assets have an owner

    if asset["warranty_end"]:
        try:
            date.fromisoformat(asset["warranty_end"])
        except ValueError:
            errors.append("Warranty end must be a date in the form YYYY-MM-DD.")
    else:
        asset["warranty_end"] = None

    if errors:
        raise ValidationError(errors)
    return asset


def _log(conn, asset_id, event):
    conn.execute("INSERT INTO history (asset_id, event, at) VALUES (?, ?, ?)", (asset_id, event, _now()))


def create_asset(conn, data):
    asset = clean(data)
    try:
        with conn:                               # one transaction: both inserts or neither
            cur = conn.execute(
                "INSERT INTO assets (tag, type, model, serial, status, assigned_to, location, warranty_end, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [asset[f] for f in FIELDS] + [_now()])
            _log(conn, cur.lastrowid, "Added to inventory")
            if asset["status"] == "assigned":
                _log(conn, cur.lastrowid, "Assigned to " + asset["assigned_to"])
    except sqlite3.IntegrityError:
        raise ValidationError(["Asset tag %s already exists." % asset["tag"]])
    return get_asset(conn, cur.lastrowid)


def get_asset(conn, asset_id):
    row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    return dict(row) if row else None


def list_assets(conn, query="", status="", type_=""):
    """All assets, optionally filtered by free text, status and type."""
    sql = "SELECT * FROM assets WHERE 1 = 1"
    params = []
    if query:
        like = "%" + query.strip() + "%"
        sql += " AND (tag LIKE ? OR model LIKE ? OR serial LIKE ? OR assigned_to LIKE ? OR location LIKE ?)"
        params += [like] * 5
    if status:
        sql += " AND status = ?"
        params.append(status)
    if type_:
        sql += " AND type = ?"
        params.append(type_)
    sql += " ORDER BY tag"
    return [dict(row) for row in conn.execute(sql, params)]


def update_asset(conn, asset_id, data):
    """Change an asset and write what changed to the history. Returns None if it does not exist."""
    before = get_asset(conn, asset_id)
    if before is None:
        return None
    after = clean(data)

    events = []
    if after["status"] != before["status"] or after["assigned_to"] != before["assigned_to"]:
        if after["status"] == "assigned":
            events.append("Assigned to " + after["assigned_to"])
        else:
            events.append("Status changed to " + after["status"].replace("_", " "))
    if after["location"] != before["location"]:
        events.append("Moved to " + (after["location"] or "no location"))
    changed = [f for f in ("tag", "type", "model", "serial", "warranty_end") if after[f] != before[f]]
    if changed:
        events.append("Details updated: " + ", ".join(changed))

    try:
        with conn:
            conn.execute(
                "UPDATE assets SET tag = ?, type = ?, model = ?, serial = ?, status = ?, assigned_to = ?, "
                "location = ?, warranty_end = ? WHERE id = ?",
                [after[f] for f in FIELDS] + [asset_id])
            for event in events:
                _log(conn, asset_id, event)
    except sqlite3.IntegrityError:
        raise ValidationError(["Asset tag %s already exists." % after["tag"]])
    return get_asset(conn, asset_id)


def delete_asset(conn, asset_id):
    """Delete an asset (its history goes with it). Returns True if something was deleted."""
    with conn:
        return conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,)).rowcount > 0


def asset_history(conn, asset_id):
    rows = conn.execute("SELECT event, at FROM history WHERE asset_id = ? ORDER BY id DESC", (asset_id,))
    return [dict(row) for row in rows]


def summary(conn, today=None, warn_days=60):
    """Numbers for the dashboard: totals per status and warranties that end soon."""
    today = today or date.today()
    limit = (today + timedelta(days=warn_days)).isoformat()
    by_status = {status: 0 for status in STATUSES}
    for row in conn.execute("SELECT status, COUNT(*) AS n FROM assets GROUP BY status"):
        by_status[row["status"]] = row["n"]
    expiring = conn.execute(
        "SELECT COUNT(*) FROM assets WHERE status != 'retired' AND warranty_end IS NOT NULL "
        "AND warranty_end BETWEEN ? AND ?", (today.isoformat(), limit)).fetchone()[0]
    expired = conn.execute(
        "SELECT COUNT(*) FROM assets WHERE status != 'retired' AND warranty_end IS NOT NULL AND warranty_end < ?",
        (today.isoformat(),)).fetchone()[0]
    return {"total": sum(by_status.values()), "by_status": by_status,
            "warranty_expiring": expiring, "warranty_expired": expired, "warn_days": warn_days}


def seed(conn, today=None):
    """Fill an empty database with example devices so the app is not blank on first run."""
    if conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]:
        return
    today = today or date.today()
    in_days = lambda n: (today + timedelta(days=n)).isoformat()
    examples = [
        ("LT-0001", "laptop", "Dell Latitude 5540", "5CD3421XYZ", "assigned", "Maria Papadopoulou", "Athens HQ", in_days(400)),
        ("LT-0002", "laptop", "Lenovo ThinkPad T14", "PF3K9A11", "assigned", "Nikos Georgiou", "Athens HQ", in_days(30)),
        ("LT-0003", "laptop", "HP EliteBook 840", "5CG1289QQ", "in_stock", "", "IT storage", in_days(-20)),
        ("DT-0001", "desktop", "Dell OptiPlex 7010", "8HX22M3", "repair", "", "Vendor", in_days(200)),
        ("MN-0001", "monitor", "Dell P2422H", "CN0V1T7", "assigned", "Maria Papadopoulou", "Athens HQ", None),
        ("PH-0001", "phone", "iPhone 14", "F2LXK91Q", "assigned", "Eleni Dimitriou", "Remote", in_days(45)),
        ("PR-0001", "printer", "HP LaserJet M404", "VNB3R12", "in_stock", "", "2nd floor", in_days(600)),
        ("NW-0001", "network", "Cisco Catalyst 9200", "FOC2301X", "in_stock", "", "Server room", in_days(900)),
        ("LT-0000", "laptop", "Dell Latitude 5400", "3VB11K2", "retired", "", "IT storage", in_days(-700)),
    ]
    for row in examples:
        create_asset(conn, dict(zip(FIELDS, row)))
