# ============================================================
# ARGUS - MIGRATE TO GLOBAL DB
# ------------------------------------------------------------
# Переносит 5 таблиц из первой базы во вторую.
# Запускается один раз.
# ============================================================

import os
import sys
import logging
from pathlib import Path

import psycopg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("migrate_global")

TABLES = [
    "onchain_metrics",
    "macro_metrics",
    "fear_greed",
    "external_market",
    "orderbook_snapshots",
]


def migrate_table(src, dst, name):
    log.info("migrating: %s", name)

    try:
        with src.cursor() as scur:
            scur.execute("SELECT * FROM " + name)
            rows = scur.fetchall()
            cols = [d.name for d in scur.description]
    except Exception as e:
        log.error("read %s: %s", name, e)
        return 0

    if not rows:
        log.info("  empty, skip")
        return 0

    cols_str = ", ".join(cols)
    placeholders = ", ".join(["%s"] * len(cols))
    sql = (
        "INSERT INTO " + name
        + " (" + cols_str + ") "
        + "VALUES (" + placeholders + ") "
        + "ON CONFLICT DO NOTHING"
    )

    added = 0
    try:
        with dst.cursor() as dcur:
            for r in rows:
                try:
                    dcur.execute(sql, r)
                    if dcur.rowcount and dcur.rowcount > 0:
                        added += dcur.rowcount
                except Exception as e:
                    log.warning("  skip row: %s", e)
        dst.commit()
    except Exception as e:
        log.error("write %s: %s", name, e)

    log.info("  %d/%d rows migrated", added, len(rows))
    return added


def main():
    src_url = os.getenv("ARGUS_DB_URL", "").strip()
    dst_url = os.getenv("ARGUS_DB_URL_2", "").strip()

    if not src_url or not dst_url:
        log.error("both ARGUS_DB_URL and ARGUS_DB_URL_2 required")
        sys.exit(1)

    log.info("connecting to source...")
    src = psycopg.connect(src_url)
    log.info("connecting to target...")
    dst = psycopg.connect(dst_url)

    total = 0
    for name in TABLES:
        total += migrate_table(src, dst, name)

    src.close()
    dst.close()

    log.info("=" * 50)
    log.info("DONE. total rows migrated: %d", total)
    log.info("=" * 50)


if __name__ == "__main__":
    main()