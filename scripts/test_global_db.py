# ============================================================
# ARGUS - TEST GLOBAL DB
# ------------------------------------------------------------
# Проверка подключения к второй базе (ARGUS_DB_URL_2).
# Пишет тестовую строку в test_check.
# ============================================================

import os
import sys
import logging
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(REPO_ROOT))

import psycopg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("test_global_db")


def main():
    url = os.getenv("ARGUS_DB_URL_2", "").strip()
    if not url:
        log.error("ARGUS_DB_URL_2 not set")
        sys.exit(1)

    log.info("connecting to global db...")

    try:
        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                # Пишем тестовую строку
                msg = (
                    "github_test "
                    + datetime.now(timezone.utc).isoformat()
                )
                cur.execute(
                    "INSERT INTO test_check (msg) "
                    "VALUES (%s) RETURNING id",
                    (msg,),
                )
                new_id = cur.fetchone()[0]
                conn.commit()
                log.info("inserted id=%d", new_id)

                # Читаем обратно
                cur.execute(
                    "SELECT id, msg, created_at "
                    "FROM test_check ORDER BY id DESC "
                    "LIMIT 3"
                )
                log.info("last rows:")
                for row in cur.fetchall():
                    log.info("  %s | %s | %s", *row)

        log.info("SUCCESS - github can write to global db")
    except Exception as e:
        log.error("FAILED: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()