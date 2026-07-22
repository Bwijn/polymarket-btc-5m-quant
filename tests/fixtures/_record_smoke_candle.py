"""Record one real BTC 5m candle → smoke_candle.json (record-replay fixture).

Context: N1 integration smoke test needs a deterministic, portable fixture so the
  test never touches live db / network. All ACTIVE factors are Binance-klines based
  (no PM-trades/EP factor — runtime TradesCache removed as dead legacy 2026-07), so
  the fixture is klines-only: event metadata + binance_klines window.
Source: db/pm_btc5m.db — events (metadata) + binance_klines (klines, incl n_trades).
Expected: writes tests/fixtures/smoke_candle.json. Re-run only to refresh fixture.
"""
import json
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parents[2] / "db" / "pm_btc5m.db"
# Chosen: most trades-in-180s among V2 BTC candles. Deterministic — hardcoded so
# the fixture is reproducible.
CID = "0xa982d0155543847b4da6277d13c736a684174426ed4d037f9ce77c62606fc0e1"

con = sqlite3.connect(DB)
cs, up_tok, dn_tok, up_won = con.execute(
    "SELECT candle_start, up_token, down_token, up_won "
    "FROM events WHERE cid=?", (CID,)).fetchone()
# Binance klines [cs-3600, cs] — scanner fetch_klines(cs-3600, cs) for bn_/basis_
# exprs (e.g. R4). Cols mirror fetch_klines output; test rebuilds DataFrame by ts.
klines = con.execute(
    "SELECT open_ts_ms/1000, open, high, low, close, volume, quote_volume, taker_buy_volume, n_trades "
    "FROM binance_klines WHERE open_ts_ms>=? AND open_ts_ms<=? ORDER BY open_ts_ms",
    ((cs - 3600) * 1000, cs * 1000)).fetchall()
con.close()

fixture = {
    "cid": CID, "cs": cs, "up_token": up_tok, "down_token": dn_tok, "up_won": up_won,
    "klines": [list(k) for k in klines],
}
out = Path(__file__).with_name("smoke_candle.json")
out.write_text(json.dumps(fixture))
print(f"wrote {out} | klines={len(klines)} cs={cs} up_won={up_won} size={out.stat().st_size}B")
