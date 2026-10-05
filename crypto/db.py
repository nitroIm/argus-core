# ============================================================
# ARGUS-Trader — DB
# ------------------------------------------------------------
# v5: reconnect fix — unconditional drop of _GLOBAL_CONN on
#     error. psycopg3 keeps .closed==False on network break,
#     only sets .broken==True, so old check failed.
#     ping() also resets on failure.
#     Drop emoji from logs (english only).
# v4: drop dead conn on error, is_configured, app_name.
# v3: global connection reuse.
# ============================================================

import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

from config import DB_URL

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.db")

_GLOBAL_CONN = None


def is_configured() -> bool:
    return bool(DB_URL)


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
        raise RuntimeError("DB_URL is not set")
    import psycopg
    _GLOBAL_CONN = psycopg.connect(
        DB_URL,
        connect_timeout=15,
        application_name="argus-db1",
    )
    log.info("DB1 connection opened")
    return _GLOBAL_CONN


def close_connection():
    global _GLOBAL_CONN
    if _GLOBAL_CONN is not None:
        try:
            if not _GLOBAL_CONN.closed:
                _GLOBAL_CONN.close()
                log.info("DB1 connection closed")
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
        # Unconditional drop: broken conn is unsafe to reuse
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
        log.error("ping failed: %s", e)
        _reset_conn()
        return False


def execute(sql: str, params: tuple = None,
            fetch: bool = False):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            if fetch:
                return cur.fetchall()
    return None


def log_collect(
    job_name: str,
    status: str,
    metric: Optional[str] = None,
    symbol: Optional[str] = None,
    records_added: int = 0,
    source_used: Optional[str] = None,
    fallback_count: int = 0,
    error: Optional[str] = None,
    started_at: Optional[datetime] = None,
) -> None:
    try:
        started = started_at or datetime.now(
            timezone.utc
        )
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO collect_log
                        (job_name, metric, symbol,
                         started_at, finished_at,
                         records_added, source_used,
                         fallback_count, status, error)
                    VALUES (%s, %s, %s, %s, NOW(),
                            %s, %s, %s, %s, %s)
                    """,
                    (
                        job_name, metric, symbol,
                        started, records_added,
                        source_used, fallback_count,
                        status, error,
                    ),
                )
    except Exception as e:
        log.error("collect_log insert: %s", e)


def log_rejected(
    job_name: str,
    reason: str,
    metric: Optional[str] = None,
    symbol: Optional[str] = None,
    raw_data=None,
    source: Optional[str] = None,
) -> None:
    try:
        raw_json = None
        if raw_data:
            raw_json = json.dumps(
                raw_data,
                ensure_ascii=False,
                default=str,
            )
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO rejected_data
                        (job_name, metric, symbol,
                         raw_data, reason, source)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        job_name, metric, symbol,
                        raw_json, reason, source,
                    ),
                )
    except Exception as e:
        log.error("rejected_data insert: %s", e)


def log_anomaly(
    symbol: str,
    timestamp: datetime,
    anomaly_type: str,
    severity: str = "medium",
    details: Optional[dict] = None,
) -> int:
    try:
        details_json = json.dumps(
            details or {},
            ensure_ascii=False,
            default=str,
        )
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO anomaly_log
                        (symbol, timestamp,
                         anomaly_type, severity,
                         details)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        symbol, timestamp,
                        anomaly_type, severity,
                        details_json,
                    ),
                )
                row = cur.fetchone()
                return row[0] if row else 0
    except Exception as e:
        log.error("anomaly_log insert: %s", e)
        return 0


if __name__ == "__main__":
    print("ARGUS DB1 - connection test")
    print("=" * 50)
    if not is_configured():
        print("FAIL: ARGUS_DB_URL not set")
        exit(1)
    if ping():
        print("OK: connection works")
        try:
            rows = execute(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' "
                "ORDER BY tablename",
                fetch=True,
            )
            print("tables in DB1: %d" % len(rows))
        except Exception as e:
            print("WARN: %s" % e)
    else:
        print("FAIL: connection broken")
        exit(1)
    close_connection()
    print("=" * 50)