# ============================================================
# ARGUS-Trader - SIMILARITY [PRODUCTION]
# ------------------------------------------------------------
# v2: symbol_conn — SOL/BNB from DB2.
#     KEY_FEATURES aligned with dataset v8.2 (30 active).
#     Dropped oi_change_pct, ls_ratio (92% NULL, useless
#     for similarity matching — they just add noise).
# v1: initial.
# ============================================================

import os
import sys
import json
import logging
from pathlib import Path
from datetime import datetime, timezone

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))

for _p in CRYPTO_ROOT.rglob("db2.py"):
    _d = str(_p.parent)
    if "__pycache__" in _d:
        continue
    if _d not in sys.path:
        sys.path.insert(0, _d)
    break

from db import get_connection

DB2_OK = False
get_conn_db2 = None
if (os.getenv("ARGUS_DB_URL_2") or "").strip():
    try:
        from db2 import (
            get_connection as get_conn_db2,
        )
        _t = get_conn_db2()
        with _t as _c:
            with _c.cursor() as _cur:
                _cur.execute("SELECT 1")
                _cur.fetchone()
        DB2_OK = True
    except Exception as e:
        print("DB2 fail: " + str(e))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.similarity")

DB2_SYMBOLS = {
    s.strip().upper()
    for s in (
        os.getenv("DB2_SYMBOLS")
        or "SOLUSDT,BNBUSDT"
    ).split(",")
    if s.strip()
}

SYMBOLS = [
    s.strip().upper()
    for s in (
        os.getenv("SYMBOLS")
        or "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT"
    ).split(",")
    if s.strip()
]


def symbol_conn(symbol):
    if symbol in DB2_SYMBOLS and DB2_OK:
        try:
            return get_conn_db2()
        except Exception as e:
            log.warning(
                "db2 conn %s: %s", symbol, e,
            )
    return get_connection()


# Aligned with dataset v8.2 active features.
# Removed: oi_change_pct, ls_ratio (92% NULL).
KEY_FEATURES = [
    "change_4h",
    "change_24h",
    "volatility_24h",
    "volume_ratio_24h",
    "funding_rate",
]

TOP_K = 20


def fetch_all(symbol):
    cols = (
        ["timestamp"]
        + KEY_FEATURES
        + ["next_change_pct", "next_direction"]
    )
    sql = (
        "SELECT " + ", ".join(cols)
        + " FROM features_hourly "
        + "WHERE symbol = %s "
        + "AND next_direction IS NOT NULL "
        + "ORDER BY timestamp"
    )
    try:
        with symbol_conn(symbol) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (symbol,))
                return cur.fetchall(), cols
    except Exception as e:
        log.error("fetch %s: %s", symbol, e)
        return [], cols


def rows_to_matrix(rows):
    n_feat = len(KEY_FEATURES)
    X = []
    meta = []
    for r in rows:
        feats = []
        for v in r[1:1 + n_feat]:
            try:
                feats.append(
                    float(v) if v is not None
                    else np.nan
                )
            except Exception:
                feats.append(np.nan)
        X.append(feats)
        meta.append({
            "timestamp": r[0],
            "next_change_pct": r[1 + n_feat],
            "next_direction": r[2 + n_feat],
        })
    return np.array(X, dtype=np.float32), meta


def normalize(X):
    mean = np.nanmean(X, axis=0)
    std = np.nanstd(X, axis=0)
    std[std == 0] = 1.0
    Xn = (X - mean) / std
    Xn = np.nan_to_num(Xn, nan=0.0)
    return Xn


def find_similar(symbol, k=TOP_K):
    rows, _ = fetch_all(symbol)
    log.info("%s: %d rows loaded", symbol, len(rows))

    if len(rows) < k + 10:
        log.warning(
            "%s: not enough data", symbol,
        )
        return None

    X, meta = rows_to_matrix(rows)
    Xn = normalize(X)

    current = Xn[-1]
    history = Xn[:-1]

    dists = np.linalg.norm(
        history - current, axis=1,
    )
    idx_sorted = np.argsort(dists)[:k]

    similar = []
    for i in idx_sorted:
        similar.append({
            "distance": round(float(dists[i]), 3),
            "timestamp": meta[i][
                "timestamp"
            ].isoformat(),
            "next_change_pct": meta[i][
                "next_change_pct"
            ],
            "next_direction": meta[i][
                "next_direction"
            ],
        })

    ups = sum(
        1 for s in similar
        if s["next_direction"] == 1
    )
    downs = k - ups

    changes = [
        s["next_change_pct"] for s in similar
        if s["next_change_pct"] is not None
    ]
    avg_change = (
        float(np.mean(changes))
        if changes else 0.0
    )

    return {
        "symbol": symbol,
        "current_at": meta[-1][
            "timestamp"
        ].isoformat(),
        "k": k,
        "similar": similar,
        "summary": {
            "up": ups,
            "down": downs,
            "up_pct": round(ups / k * 100, 1),
            "avg_next_change_pct": round(
                avg_change, 4,
            ),
        },
    }


def main():
    log.info("=" * 60)
    log.info("ARGUS SIMILARITY v2")
    log.info("=" * 60)

    results = {}
    for symbol in SYMBOLS:
        r = find_similar(symbol, k=TOP_K)
        if r:
            results[symbol] = r
            s = r["summary"]
            log.info(
                "[%s] k=%d up=%d down=%d "
                "(%.1f%% up) avg_next=%.4f%%",
                symbol, r["k"],
                s["up"], s["down"], s["up_pct"],
                s["avg_next_change_pct"],
            )

    out = SCRIPT_DIR / "last_similarity.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(
            results, f,
            ensure_ascii=False,
            indent=2, default=str,
        )
    log.info("saved: %s", out.name)


if __name__ == "__main__":
    main()