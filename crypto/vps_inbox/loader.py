# ============================================================
# ARGUS - VPS INBOX LOADER
# ------------------------------------------------------------
# Читает CSV из crypto/vps_inbox, грузит в БД.
# После успешной загрузки файлы удаляются с диска.
# Роутинг: BTC/ETH -> DB1, SOL/BNB -> DB2.
# ============================================================

import os
import csv
import sys
import logging
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))

for _p in CRYPTO_ROOT.rglob("db2.py"):
    _d = str(_p.parent)
    if "__pycache__" in _d:
        continue
    if _d not in sys.path:
        sys.path.insert(0, _d)
    break

from db import get_connection
from db2 import get_connection as get_conn_db2

DB2_OK = False
try:
    _t = get_conn_db2()
    with _t as _c:
        with _c.cursor() as _cur:
            _cur.execute("SELECT 1")
            _cur.fetchone()
    DB2_OK = True
except Exception as e:
    print("DB2 fail: " + str(e))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("vps.loader")

DB2_SYMBOLS = {"SOLUSDT", "BNBUSDT"}

TYPES = [
    {
        "name": "funding",
        "dir": "funding",
        "table": "funding_rates",
        "cols": ["timestamp", "rate"],
        "insert_cols": "symbol, timestamp, rate, source",
        "ph": "(%s,%s,%s,%s)",
    },
    {
        "name": "open_interest",
        "dir": "open_interest",
        "table": "open_interest",
        "cols": ["timestamp", "oi", "oi_value"],
        "insert_cols": "symbol, timestamp, oi, oi_value, source",
        "ph": "(%s,%s,%s,%s,%s)",
    },
    {
        "name": "long_short_ratio",
        "dir": "long_short_ratio",
        "table": "long_short_ratio",
        "cols": ["timestamp", "ls_ratio", "long_pct", "short_pct"],
        "insert_cols": "symbol, timestamp, ls_ratio, long_pct, short_pct, source",
        "ph": "(%s,%s,%s,%s,%s,%s)",
    },
    {
        "name": "taker_flow",
        "dir": "taker_flow",
        "table": "taker_flow",
        "cols": ["timestamp", "buy_vol", "sell_vol"],
        "insert_cols": "symbol, timestamp, buy_vol, sell_vol, source",
        "ph": "(%s,%s,%s,%s,%s)",
    },
]


def symbol_conn(symbol):
    if symbol in DB2_SYMBOLS and DB2_OK:
        return get_conn_db2()
    return get_connection()


def table_exists(symbol, table):
    try:
        with symbol_conn(symbol) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT to_regclass(%s)",
                    ("public." + table,),
                )
                row = cur.fetchone()
                return bool(row and row[0])
    except Exception as e:
        log.warning("table_exists %s.%s: %s", symbol, table, e)
        return False


def read_csv(path):
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                rows.append(r)
    except Exception as e:
        log.error("read %s: %s", path, e)
        return []
    return rows


def symbol_from_filename(fname):
    base = fname.rsplit(".", 1)[0]
    if "_" not in base:
        return None
    return base.split("_", 1)[0]


def load_csv(symbol, t, rows):
    if not rows:
        return 0, "empty"
    if not table_exists(symbol, t["table"]):
        return 0, "no table " + t["table"]

    n = len(rows)
    placeholders = ",".join([t["ph"]] * n)
    sql = (
        "INSERT INTO " + t["table"]
        + " (" + t["insert_cols"] + ")"
        + " VALUES " + placeholders
        + " ON CONFLICT (symbol, timestamp) DO NOTHING"
    )

    params = []
    for r in rows:
        params.append(symbol)
        for c in t["cols"]:
            params.append(r.get(c))
        params.append("binance")

    try:
        with symbol_conn(symbol) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                return cur.rowcount or 0, "ok"
    except Exception as e:
        return 0, "err " + str(e)[:80]


def process_type(t):
    dir_path = SCRIPT_DIR / t["dir"]
    result = {
        "name": t["name"],
        "files": 0,
        "loaded": 0,
        "errors": [],
        "deleted": [],
    }
    if not dir_path.exists():
        return result

    files = sorted(dir_path.glob("*.csv"))
    result["files"] = len(files)

    for fp in files:
        sym = symbol_from_filename(fp.name)
        if not sym:
            result["errors"].append(fp.name + ": no symbol")
            continue

        rows = read_csv(fp)
        if not rows:
            result["errors"].append(fp.name + ": empty")
            continue

        n, status = load_csv(sym, t, rows)
        if status.startswith("err") or status.startswith("no table"):
            result["errors"].append(fp.name + ": " + status)
            continue

        result["loaded"] += n
        result["deleted"].append(fp.name)
        log.info("%s %s: loaded %d rows", t["name"], fp.name, n)

    return result


def delete_files(t, names):
    dir_path = SCRIPT_DIR / t["dir"]
    removed = 0
    for name in names:
        fp = dir_path / name
        if fp.exists():
            try:
                fp.unlink()
                removed += 1
            except Exception as e:
                log.warning("remove %s: %s", fp.name, e)
    return removed


def main():
    log.info("=" * 60)
    log.info("VPS INBOX LOADER")
    log.info("DIR: %s", SCRIPT_DIR)
    log.info("DB2_OK: %s", DB2_OK)
    log.info("=" * 60)

    summary = []
    for t in TYPES:
        r = process_type(t)
        r["removed"] = delete_files(t, r["deleted"])
        summary.append(r)

    log.info("=" * 60)
    log.info("SUMMARY")
    for r in summary:
        log.info(
            "  %s: files=%d loaded=%d removed=%d errors=%d",
            r["name"], r["files"],
            r["loaded"], r["removed"],
            len(r["errors"]),
        )
        for e in r["errors"]:
            log.warning("    err: %s", e)
    log.info("=" * 60)

    close_connection()
    if DB2_OK:
        try:
            from db2 import close_connection as db2c
            db2c()
        except Exception:
            pass


if __name__ == "__main__":
    main()