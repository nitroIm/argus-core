# ============================================================
# ARGUS-Trader — DB
# ------------------------------------------------------------
# Обёртка psycopg для работы с Supabase.
# Только соединение и низкоуровневые операции.
# Никакой бизнес-логики.
# ------------------------------------------------------------
# v1: начальная версия
# ============================================================

import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional, Iterable

from config import DB_URL

# ============================================================
# ЛОГИРОВАНИЕ
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.db")


# ============================================================
# ПОДКЛЮЧЕНИЕ
# ============================================================
def is_configured() -> bool:
    """Проверяет, задан ли ARGUS_DB_URL."""
    return bool(DB_URL)


@contextmanager
def get_connection():
    """
    Контекстный менеджер для соединения с БД.
    Автоматически коммитит при успехе, откатывает при ошибке.
    Закрывает соединение в любом случае.
    """
    if not DB_URL:
        raise RuntimeError("ARGUS_DB_URL не задан")

    try:
        import psycopg
    except ImportError:
        raise RuntimeError(
            "psycopg не установлен. Добавь psycopg[binary] в requirements.txt"
        )

    conn = None
    try:
        conn = psycopg.connect(DB_URL, connect_timeout=15)
        yield conn
        conn.commit()
    except Exception:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        raise
    finally:
        if conn:
            conn.close()


def ping() -> bool:
    """Проверка, что соединение работает."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return True
    except Exception as e:
        log.error(f"Ping failed: {e}")
        return False


# ============================================================
# CRUD ХЕЛПЕРЫ
# ============================================================
def execute(sql: str, params: tuple = None, fetch: bool = False):
    """
    Выполняет SQL. Если fetch=True — возвращает результат.
    Использует параметризацию для защиты от инъекций.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            if fetch:
                return cur.fetchall()
    return None


def insert_ignore(sql: str, params: tuple = None) -> int:
    """
    INSERT с ON CONFLICT DO NOTHING. Возвращает количество добавленных строк.
    Ожидаемая SQL-форма: INSERT ... ON CONFLICT DO NOTHING RETURNING 1
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            try:
                rows = cur.fetchall()
                return len(rows)
            except Exception:
                return 0


def insert_many_ignore(sql: str, params_list: Iterable[tuple]) -> int:
    """
    Массовый INSERT с ON CONFLICT DO NOTHING.
    Возвращает количество добавленных строк.
    """
    if not params_list:
        return 0

    added = 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            for params in params_list:
                try:
                    cur.execute(sql, params)
                    if cur.rowcount and cur.rowcount > 0:
                        added += cur.rowcount
                except Exception as e:
                    log.warning(f"Row skipped: {e}")
    return added


# ============================================================
# СПЕЦИАЛЬНЫЕ ХЕЛПЕРЫ
# ============================================================
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
    """Запись в журнал сбора. Не роняет сбор при ошибке записи."""
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
    """Запись в карантин. Не роняет сбор при ошибке."""
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
    """Запись в anomaly_log. Возвращает id."""
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


# ============================================================
# ТЕСТ
# ============================================================
if __name__ == "__main__":
    print("🔌 ARGUS-Trader DB — тест соединения")
    print("=" * 50)
    if not is_configured():
        print("❌ ARGUS_DB_URL не задан")
        exit(1)

    if ping():
        print("✅ Соединение работает")
        # Проверяем, есть ли таблицы
        try:
            rows = execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename",
                fetch=True,
            )
            print(f"📋 Таблиц в БД: {len(rows)}")
            for r in rows:
                print(f"   • {r[0]}")
        except Exception as e:
            print(f"⚠️ Не могу получить список таблиц: {e}")
    else:
        print("❌ Соединение не работает")
        exit(1)
    print("=" * 50)