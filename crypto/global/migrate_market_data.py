# ============================================================
# ARGUS-Trader — MIGRATE MARKET DATA
# ------------------------------------------------------------
# v1: one-shot migration. Read external_market +
#     macro_metrics from DB1, write into global_market
#     in DB2. Does NOT delete old tables.
# ============================================================

import sys
import logging
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from db import get_connection as db1_conn
from db2 import get_connection as db2_conn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("migrate.market")

DB1_TABLES = ["external_market", "macro_metrics"]

SQL_INSERT = (
    "INSERT INTO global_market "
    "(symbol, timestamp, close, change_pct, source) "
    "VALUES (%s,%s,%s,%s,%s) "
    "ON CONFLICT (symbol, timestamp) DO NOTHING"
)


def read_all(table):
    rows = []
    try:
        with db1_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT symbol, timestamp, close, "
                    f"change_pct, source FROM {table}"
                )
                rows = cur.fetchall()
    except Exception as e:
        log.error("read %s: %s", table, e)
    return rows


def write_batch(rows):
    if not rows:
        return 0
    added = 0
    try:
        with db2_conn() as conn:
            with conn.cursor() as cur:
                for r in rows:
                    try:
                        cur.execute(SQL_INSERT, r)
                        if cur.rowcount and \
                                cur.rowcount > 0:
                            added += cur.rowcount
                    except Exception as e:
                        log.warning("skip: %s", e)
    except Exception as e:
        log.error("write: %s", e)
    return added


def main():
    log.info("=" * 60)
    log.info("MIGRATE MARKET DATA v1")
    log.info("=" * 60)

    total_read = 0
    total_written = 0

    for table in DB1_TABLES:
        log.info(f"--- {table} ---")
        rows = read_all(table)
        log.info(f"  read: {len(rows)}")
        total_read += len(rows)
        n = write_batch(rows)
        log.info(f"  written: {n}")
        total_written += n

    log.info("=" * 60)
    log.info(f"DONE. read={total_read} "
             f"written={total_written}")
    log.info("=" * 60)


if __name__ == "__main__":
    main()