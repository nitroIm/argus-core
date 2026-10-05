# ============================================================
# ARGUS-Trader — DB2 (global node, second database)
# ------------------------------------------------------------
# v3: reconnect fix — unconditional drop of _GLOBAL_CONN on
#     error. Same fix as db.py v5. ping() resets on failure.
#     Drop emoji from logs.
# v2: drop dead conn (broken), is_configured, app_name.
# v1: mirror of db.py, reads ARGUS_DB_URL_2.
#     Isolated: does NOT touch DB1.
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

DB2_URL = (
    os.getenv("ARGUS_DB_URL_2") or ""
).strip()

_GLOBAL_CONN = None


def is_configured() -> bool:
    return bool(DB2_URL)


def _reset_conn():
    global _GLOBAL_CONN
    _GLOBAL_CONN = None


def _get_conn():
    global _GLOBAL_CONN
    if _GLOBAL_CONN is not None:
        try:
            if not _GLOBAL_CONN.closed:
                return _GLOBAL_CONN
        except Exception:
            pass
    if not is_configured():
        raise RuntimeError(
            "ARGUS_DB_URL_2 is not set"
        )
    import psycopg
    _GLOBAL_CONN = psycopg.connect(
        DB2_URL,
        connect_timeout=15,
        application_name="argus-db2",
    )
    log.info("DB2 connection opened")
    return _GLOBAL_CONN


def close_connection():
    global _GLOBAL_CONN
    if _GLOBAL_CONN is not None:
        try:
            if not _GLOBAL_CONN.closed:
                _GLOBAL_CONN.close()
                log.info("DB2 connection closed")
        except Exception:
            pass
    _GLOBAL_CONN = None


@contextmanager
def get_connection():
    conn = _get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        _reset_conn()
        raise


def ping() -> bool:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return True
    except Exception as e:
        log.error("DB2 ping failed: %s", e)
        _reset_conn()
        return False


def execute(sql, params=None, fetch=False):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            if fetch:
                return cur.fetchall()
    return None


if __name__ == "__main__":
    print("ARGUS DB2 - connection test")
    print("=" * 50)
    if not is_configured():
        print("FAIL: ARGUS_DB_URL_2 not set")
        exit(1)
    if ping():
        print("OK: DB2 works")
    else:
        print("FAIL: DB2 broken")
        exit(1)
    close_connection()
    print("=" * 50)