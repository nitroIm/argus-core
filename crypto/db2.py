# ============================================================
# ARGUS-Trader - DB2 (вторая база, общие данные)
# ------------------------------------------------------------
# Отдельное подключение к argus-global-data.
# Используется для: onchain, macro, fear_greed,
# external_market, orderbook_snapshots.
# ============================================================

import os
import logging
import psycopg

log = logging.getLogger("crypto.db2")

ARGUS_DB_URL_2 = os.getenv("ARGUS_DB_URL_2", "").strip()


def get_connection_2():
    """Подключение ко второй базе (общие данные)."""
    if not ARGUS_DB_URL_2:
        raise RuntimeError("ARGUS_DB_URL_2 not set")
    return psycopg.connect(ARGUS_DB_URL_2)


def is_configured_2():
    return bool(ARGUS_DB_URL_2)