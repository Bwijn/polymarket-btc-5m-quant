import sqlite3
import json
import time
from pathlib import Path

class Store:
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS smart_wallets (
                address TEXT PRIMARY KEY,
                roi REAL, winrate REAL, sharpe REAL,
                avg_edge REAL, trade_count INTEGER,
                category TEXT, updated_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS wallet_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet TEXT, market_id TEXT, token_id TEXT,
                side TEXT, price REAL, size REAL,
                timestamp INTEGER,
                UNIQUE(wallet, market_id, timestamp)
            );
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT, question TEXT,
                side TEXT, consensus_pct REAL,
                agreeing_wallets TEXT,
                price_at_signal REAL, timestamp INTEGER
            );
            CREATE TABLE IF NOT EXISTS paper_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER, market_id TEXT, question TEXT,
                side TEXT, entry_price REAL, size REAL,
                exit_price REAL, pnl REAL,
                status TEXT DEFAULT 'open',
                opened_at INTEGER, closed_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS portfolio (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        row = self.conn.execute("SELECT value FROM portfolio WHERE key='balance'").fetchone()
        if not row:
            self.conn.execute("INSERT INTO portfolio VALUES ('balance', '500.0')")
            self.conn.commit()

    def get_balance(self) -> float:
        row = self.conn.execute("SELECT value FROM portfolio WHERE key='balance'").fetchone()
        return float(row["value"])

    def set_balance(self, bal: float):
        self.conn.execute("UPDATE portfolio SET value=? WHERE key='balance'", (str(bal),))
        self.conn.commit()

    def upsert_wallet(self, addr: str, roi: float, winrate: float, sharpe: float,
                      avg_edge: float, trade_count: int, category: str = ""):
        self.conn.execute("""
            INSERT INTO smart_wallets VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(address) DO UPDATE SET
                roi=excluded.roi, winrate=excluded.winrate, sharpe=excluded.sharpe,
                avg_edge=excluded.avg_edge, trade_count=excluded.trade_count,
                category=excluded.category, updated_at=excluded.updated_at
        """, (addr, roi, winrate, sharpe, avg_edge, trade_count, category, int(time.time())))
        self.conn.commit()

    def get_smart_wallets(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM smart_wallets ORDER BY roi DESC").fetchall()
        return [dict(r) for r in rows]

    def record_wallet_trade(self, wallet: str, market_id: str, token_id: str,
                            side: str, price: float, size: float, ts: int):
        try:
            self.conn.execute(
                "INSERT OR IGNORE INTO wallet_trades (wallet,market_id,token_id,side,price,size,timestamp) VALUES (?,?,?,?,?,?,?)",
                (wallet, market_id, token_id, side, price, size, ts))
            self.conn.commit()
        except sqlite3.IntegrityError:
            pass

    def get_recent_trades(self, market_id: str, window_seconds: int) -> list[dict]:
        cutoff = int(time.time()) - window_seconds
        rows = self.conn.execute(
            "SELECT * FROM wallet_trades WHERE market_id=? AND timestamp>=? ORDER BY timestamp",
            (market_id, cutoff)).fetchall()
        return [dict(r) for r in rows]

    def record_signal(self, market_id: str, question: str, side: str,
                      consensus_pct: float, wallets: list[str], price: float) -> int:
        cur = self.conn.execute(
            "INSERT INTO signals (market_id,question,side,consensus_pct,agreeing_wallets,price_at_signal,timestamp) VALUES (?,?,?,?,?,?,?)",
            (market_id, question, side, consensus_pct, json.dumps(wallets), price, int(time.time())))
        self.conn.commit()
        return cur.lastrowid

    def open_paper_trade(self, signal_id: int, market_id: str, question: str,
                         side: str, price: float, size: float) -> int:
        cur = self.conn.execute(
            "INSERT INTO paper_trades (signal_id,market_id,question,side,entry_price,size,opened_at) VALUES (?,?,?,?,?,?,?)",
            (signal_id, market_id, question, side, price, size, int(time.time())))
        self.conn.commit()
        return cur.lastrowid

    def close_paper_trade(self, trade_id: int, exit_price: float):
        row = self.conn.execute("SELECT * FROM paper_trades WHERE id=?", (trade_id,)).fetchone()
        if not row:
            return
        entry = row["entry_price"]
        size = row["size"]
        side = row["side"]
        if side == "BUY":
            pnl = (exit_price - entry) * size
        else:
            pnl = (entry - exit_price) * size
        self.conn.execute(
            "UPDATE paper_trades SET exit_price=?, pnl=?, status='closed', closed_at=? WHERE id=?",
            (exit_price, pnl, int(time.time()), trade_id))
        bal = self.get_balance() + pnl
        self.set_balance(bal)
        self.conn.commit()

    def get_open_trades(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM paper_trades WHERE status='open'").fetchall()
        return [dict(r) for r in rows]

    def get_all_trades(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM paper_trades ORDER BY opened_at DESC").fetchall()
        return [dict(r) for r in rows]
