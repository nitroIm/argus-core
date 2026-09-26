# ============================================================
# ARGUS-Trader - ONCHAIN COLLECTOR [PRODUCTION]
# ------------------------------------------------------------
# Hash Rate + Difficulty BTC с Mempool.space.
# Публичный API, без ключа. Раз в сутки.
# ============================================================

import sys
import logging
import requests
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))

from db import get_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.onchain")

BASE = "https://mempool.space/api/v1"
HEADERS = {"User-Agent": "Mozilla/5.0"}


def fetch_hashrate():
    """Возвращает (timestamp, hashrate_hs, difficulty)."""
    try:
        r = requests.get(
            BASE + "/mining/hashrate/3d",
            headers=HEADERS, timeout=20,
        )
        if r.status_code != 200:
            log.warning("HTTP %d", r.status_code)
            return None
        data = r.json()
    except Exception as e:
        log.error("fetch: %s", e)
        return None

    hr_list = data.get("hashrates", [])
    if not hr_list:
        return None

    last = hr_list[-1]
    ts = int(last.get("timestamp", 0))
    hr = float(last.get("avgHashrate", 0))
    diff = float(data.get("currentDifficulty", 0))
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return (dt, hr, diff)


def save(ts, hr, diff):
    sql = (
        "INSERT INTO onchain_metrics "
        "(symbol, timestamp, hashrate, difficulty, source) "
        "VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (symbol, timestamp) DO UPDATE SET "
        "hashrate = EXCLUDED.hashrate, "
        "difficulty = EXCLUDED.difficulty"
    )
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    "BTC", ts, hr, diff, "mempool.space",
                ))
                return cur.rowcount if cur.rowcount else 0
    except Exception as e:
        log.error("save: %s", e)
        return 0


def collect_onchain():
    """Главная функция сбора."""
    res = fetch_hashrate()
    if not res:
        log.warning("no data")
        return 0
    ts, hr, diff = res
    added = save(ts, hr, diff)
    log.info(
        "[onchain/BTC] hr=%.1f EH/s diff=%.1f T added=%d",
        hr / 1e18, diff / 1e12, added,
    )
    return added


if __name__ == "__main__":
    log.info("=" * 50)
    log.info("ONCHAIN test")
    log.info("=" * 50)
    n = collect_onchain()
    log.info("done, added=%d", n)