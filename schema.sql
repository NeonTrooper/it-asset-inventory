-- Database structure for the IT asset inventory (SQLite).

CREATE TABLE IF NOT EXISTS assets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    tag          TEXT    NOT NULL UNIQUE,              -- the sticker on the device, e.g. LT-0042
    type         TEXT    NOT NULL CHECK (type IN ('laptop', 'desktop', 'monitor', 'phone', 'printer', 'network', 'other')),
    model        TEXT    NOT NULL,
    serial       TEXT    NOT NULL DEFAULT '',
    status       TEXT    NOT NULL DEFAULT 'in_stock' CHECK (status IN ('in_stock', 'assigned', 'repair', 'retired')),
    assigned_to  TEXT    NOT NULL DEFAULT '',          -- empty unless status = 'assigned'
    location     TEXT    NOT NULL DEFAULT '',
    warranty_end TEXT,                                 -- YYYY-MM-DD, or NULL when unknown
    created_at   TEXT    NOT NULL
);

-- One row for every change to an asset, so there is an audit trail.
CREATE TABLE IF NOT EXISTS history (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL REFERENCES assets (id) ON DELETE CASCADE,
    event    TEXT    NOT NULL,
    at       TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_assets_status ON assets (status);
CREATE INDEX IF NOT EXISTS idx_history_asset ON history (asset_id);
