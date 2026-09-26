# ============================================================
# ARGUS-Trader - FEAR & GREED [PRODUCTION]
# ------------------------------------------------------------
# Сбор индекса страха/жадности (alternative.me).
# Без ключа, бесплатно, раз в сутки.
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
log = logging.getLogger("crypto.feargreed")

FNG_URL = "https://api.alternative.me/fng/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)",
}


def fetch_fng(limit=1):
    """Тянет последние N значений индекса."""
    params = {
        "limit": limit,
        "format": "json",
    }
    try:
        r = requests.get(
            FNG_URL,
            params=params,
            headers=HEADERS,
            timeout=15,
        )
        if r.status_code != 200:
            log.warning("HTTP %d", r.status_code)
            return []
        data = r.json()
    except Exception as e:
        log.error("fetch: %s", e)
        return []

    rows = data.get("data", [])
    out = []
    for r in rows:
        try:
            ts = int(r.get("timestamp", 0))
            dt = datetime.fromtimestamp(
                ts, tz=timezone.utc,
            )
            value = int(r.get("value", 0))
            cls = r.get(
                "value_classification", "?",
            )
            out.append({
                "timestamp": dt,
                "value": value,
                "classification": cls,
            })
        except Exception:
            continue
    return out


def save_rows(rows):
    if not rows:
        return 0

    sql = (
        "INSERT INTO fear_greed "
        "(timestamp, value, classification, source) "
        "VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (timestamp) DO UPDATE SET "
        "value = EXCLUDED.value, "
        "classification = EXCLUDED.classification"
    )

    added = 0
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for r in rows:
                    try:
                        cur.execute(sql, (
                            r["timestamp"],
                            r["value"],
                            r["classification"],
                            "alternative.me",
                        ))
                        if cur.rowcount and cur.rowcount > 0:
                            added += cur.rowcount
                    except Exception as e:
                        log.warning("skip: %s", e)
    except Exception as e:
        log.error("save: %s", e)
    return added


def collect_feargreed():
    """Главная функция. Возвращает число строк."""
    rows = fetch_fng(limit=1)
    if not rows:
        log.warning("no data")
        return 0

    added = save_rows(rows)
    r = rows[-1]
    log.info(
        "[fear_greed] value=%d (%s) added=%d",
        r["value"], r["classification"], added,
    )
    return added


if __name__ == "__main__":
    log.info("=" * 50)
    log.info("FEAR & GREED test")
    log.info("=" * 50)
    n = collect_feargreed()
    log.info("done, added=%d", n)