"""001 — drop paper_trade_5m_binary.hypothesis (expr = sole strategy identity).

Rationale: hypothesis (R/H label) was a second name for a strategy that expr
already identifies uniquely. Keeping both = sync risk + stale numbering. After
this, dedup keys + the dashboard view all key on expr; label lives only in
strategies.py as a cosmetic log handle (never persisted).

up:   DROP COLUMN hypothesis — values discarded (expr fully identifies the row).
down: re-add hypothesis as nullable TEXT. NOTE: original was NOT NULL and the
      dropped values are NOT recoverable — rollback restores shape, not data.
"""
VERSION = "001_drop_hypothesis"


def up(conn):
    conn.execute("ALTER TABLE paper_trade_5m_binary DROP COLUMN hypothesis")


def down(conn):
    conn.execute("ALTER TABLE paper_trade_5m_binary ADD COLUMN hypothesis TEXT")
