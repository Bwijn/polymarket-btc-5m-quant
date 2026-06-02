"""Minimal versioned schema migration runner — stdlib sqlite3, zero deps.

Context: a money bot's schema changes must be versioned + repeatable + applied
atomically with a stop-the-world deploy (todo "migration 策略"). NOT in polybot/
so it never auto-deploys; N6 scp's this dir to the VPS to run against polybot.db.
Source: NNN_*.py modules beside this file, each exposing VERSION + up(conn)/down(conn).
Expected: `python migrate.py <db> [status|up|down]`
  status — list migrations and applied state (default)
  up     — apply all pending in ascending order, record each in schema_migrations
  down   — roll back the single latest applied migration
"""
import importlib.util
import sqlite3
import sys
import time
from pathlib import Path

MIN_SQLITE = (3, 35, 0)          # ALTER TABLE ... DROP COLUMN support
HERE = Path(__file__).resolve().parent


def _discover():
    mods = []
    for p in sorted(HERE.glob("[0-9][0-9][0-9]_*.py")):
        spec = importlib.util.spec_from_file_location(p.stem, p)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        mods.append(m)
    return mods


def _applied(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
                 "(version TEXT PRIMARY KEY, applied_at INTEGER)")
    return {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}


def _guard_sqlite():
    v = tuple(int(x) for x in sqlite3.sqlite_version.split("."))
    if v < MIN_SQLITE:
        sys.exit(f"ABORT: sqlite {sqlite3.sqlite_version} < "
                 f"{'.'.join(map(str, MIN_SQLITE))} (DROP COLUMN unsupported)")


def cmd_status(db):
    conn = sqlite3.connect(db)
    done = _applied(conn)
    for m in _discover():
        head = (m.__doc__ or "").splitlines()[0] if m.__doc__ else ""
        print(f"  [{'x' if m.VERSION in done else ' '}] {m.VERSION}  {head}")
    conn.close()


def cmd_up(db):
    _guard_sqlite()
    conn = sqlite3.connect(db)
    done = _applied(conn)
    pending = [m for m in _discover() if m.VERSION not in done]
    if not pending:
        print("up-to-date — nothing to apply")
    for m in pending:
        print(f"applying {m.VERSION} ...")
        m.up(conn)
        conn.execute("INSERT INTO schema_migrations VALUES (?,?)",
                     (m.VERSION, int(time.time())))
        conn.commit()
        print(f"  ✓ {m.VERSION}")
    conn.close()


def cmd_down(db):
    conn = sqlite3.connect(db)
    done = _applied(conn)
    applied = [m for m in _discover() if m.VERSION in done]
    if not applied:
        print("nothing to roll back")
        conn.close()
        return
    m = applied[-1]
    print(f"rolling back {m.VERSION} ...")
    m.down(conn)
    conn.execute("DELETE FROM schema_migrations WHERE version=?", (m.VERSION,))
    conn.commit()
    print(f"  ✓ rolled back {m.VERSION}")
    conn.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: migrate.py <db_path> [status|up|down]")
    cmd = sys.argv[2] if len(sys.argv) > 2 else "status"
    {"status": cmd_status, "up": cmd_up, "down": cmd_down}[cmd](sys.argv[1])
