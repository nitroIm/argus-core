# ============================================================
# ARGUS-Trader — DB2 (узел global, вторая база)
# ------------------------------------------------------------
# v2: drop dead conn on error -> next call reconnects.
#     is_configured() guard, application_name.
# v1: зеркало db.py, читает ARGUS_DB_URL_2.
#     Изолировано: НЕ трогает основную базу.
# ============================================================

import os
import logging
from contextlib import contextmanager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.db2")

DB2_URL = (os.getenv("ARGUS_DB_URL_2") or "").strip()

_GLOBAL_CONN = None


def is_configured() -> bool:
    return bool(DB2_URL)


def _get_conn():
    global _GLOBAL_CONN
    if _GLOBAL_CONN is not None and not _GLOBAL_CONN.closed:
        return _GLOBAL_CONN
    if not is_configured():
        raise RuntimeError("ARGUS_DB_URL_2 is not set")
    import psycopg
    _GLOBAL_CONN = psycopg.connect(
        DB2_URL,
        connect_timeout=15,
        application_name="argus-db2",
    )
    log.info("🔌 DB2: соединение открыто")
    return _GLOBAL_CONN


def close_connection():
    global _GLOBAL_CONN
    if _GLOBAL_CONN is not None:
        if not _GLOBAL_CONN.closed:
            _GLOBAL_CONN.close()
            log.info("🔌 DB2: соединение закрыто")
    _GLOBAL_CONN = None


@contextmanager
def get_connection():
    global _GLOBAL_CONN
    conn = _get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        try:
            if conn.closed:
                _GLOBAL_CONN = None
        except Exception:
            _GLOBAL_CONN = None
        raise


def ping() -> bool:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return True
    except Exception as e:
        log.error(f"DB2 ping failed: {e}")
        return False


def execute(sql, params=None, fetch=False):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            if fetch:
                return cur.fetchall()
    return None


if __name__ == "__main__":
    print("🔌 ARGUS-Trader DB2 — тест")
    print("=" * 50)
    if not is_configured():
        print("❌ ARGUS_DB_URL_2 не задан")
        exit(1)
    if ping():
        print("✅ DB2 работает")
    else:
        print("❌ DB2 не работает")
        exit(1)
    close_connection()
    print("=" * 50)