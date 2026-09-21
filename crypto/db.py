# ============================================================
# ARGUS-Trader — DB
# ------------------------------------------------------------
# v3: + переиспользование одного соединения на весь прогон.
#     Глобальное соединение через _get_conn().
#     Ускорение в 10-15 раз (одно соединение вместо 30).
#     + close_connection() для явного закрытия в конце.
# ============================================================

import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional, Iterable

from config import DB_URL

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.db")

# --- Глобальное соединение ---
_GLOBAL_CONN = None


def is_configured() -> bool:
    return bool(DB_URL)


def _get_conn():
    """Возвращает глобальное соединение (создаёт при первом вызове)."""
    global _GLOBAL_CONN
    if _GLOBAL_CONN is None or _GLOBAL_CONN.closed:
        import psycopg
        _GLOBAL_CONN = psycopg.connect(DB_URL, connect_timeout=15)
        log.info("🔌 Открыто соединение с Supabase")
    return _GLOBAL_CONN


def close_connection():
    """Явно закрывает глобальное соединение."""
    global _GLOBAL_CONN
    if _GLOBAL_CONN is not None and not _GLOBAL_CONN.closed:
        _GLOBAL_CONN.close()
        log.info("🔌 Соединение закрыто")
    _GLOBAL_CONN = None


@contextmanager
def get_connection():
    """Контекстный менеджер. Использует глобальное соединение."""
    conn = _get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise


def ping() -> bool:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return True
    except Exception as e:
        log.error(f"Ping failed: {e}")
        return False


def execute(sql: str, params: tuple = None, fetch: bool = False):
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
        started = started_at or datetime.now(timezone.utc)
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO collect_log
                        (job_name, metric, symbol, started_at, finished_at,
                         records_added, source_used, fallback_count, status, error)
                    VALUES (%s, %s, %s, %s, NOW(), %s, %s, %s, %s, %s)
                    """,
                    (
                        job_name, metric, symbol, started,
                        records_added, source_used, fallback_count,
                        status, error,
                    ),
                )
    except Exception as e:
        log.error(f"Не удалось записать collect_log: {e}")


def log_rejected(
    job_name: str,
    reason: str,
    metric: Optional[str] = None,
    symbol: Optional[str] = None,
    raw_data=None,
    source: Optional[str] = None,
) -> None:
    try:
        raw_json = json.dumps(raw_data, ensure_ascii=False, default=str) if raw_data else None
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO rejected_data
                        (job_name, metric, symbol, raw_data, reason, source)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (job_name, metric, symbol, raw_json, reason, source),
                )
    except Exception as e:
        log.error(f"Не удалось записать rejected_data: {e}")


def log_anomaly(
    symbol: str,
    timestamp: datetime,
    anomaly_type: str,
    severity: str = "medium",
    details: Optional[dict] = None,
) -> int:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO anomaly_log
                        (symbol, timestamp, anomaly_type, severity, details)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        symbol, timestamp, anomaly_type, severity,
                        json.dumps(details or {}, ensure_ascii=False, default=str),
                    ),
                )
                row = cur.fetchone()
                return row[0] if row else 0
    except Exception as e:
        log.error(f"Не удалось записать anomaly_log: {e}")
        return 0


if __name__ == "__main__":
    print("🔌 ARGUS-Trader DB — тест соединения")
    print("=" * 50)
    if not is_configured():
        print("❌ ARGUS_DB_URL не задан")
        exit(1)
    if ping():
        print("✅ Соединение работает")
        try:
            rows = execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename",
                fetch=True,
            )
            print(f"📋 Таблиц в БД: {len(rows)}")
        except Exception as e:
            print(f"⚠️ {e}")
    else:
        print("❌ Соединение не работает")
        exit(1)
    close_connection()
    print("=" * 50)