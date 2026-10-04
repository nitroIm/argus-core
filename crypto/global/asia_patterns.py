# ============================================================
# ARGUS-Trader — ASIA + EUROPE PATTERNS (узел global)
# ------------------------------------------------------------
# v2.7: MIN_SAMPLES=10 (align with explorer filter).
# v2.6: skip gap points (dt > 2h) in load_asia.
#       Log forecast price per pattern.
# v2.5: MIN_SAMPLES=5 (align with explorer filter).
#       Parametrized SQL (no % formatting).
#       log_run at end.
# v2.4: + USA (VIX, NASDAQ, US10Y) +
#       ASIA_EXTRA (USDJPY, KOSPI, TAIEX).
# ============================================================

import os
import sys
import logging
import bisect
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from db2 import get_connection, close_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("global.patterns")

WINDOW_DAYS = 30
LAGS = [1, 2, 3, 6, 12]
THRESHOLDS = [1.0, 2.0]
MIN_SAMPLES = 10
MIN_CORR_SAMPLES = 20
TOL_SEC = 1800
GAP_HOURS = 2.0

ASIA_SYMBOLS = [
    "NIKKEI", "SHANGHAI", "HANGSENG", "USDCNY",
    "DAX", "SX5E", "FTSE", "EURUSD",
    "VIX", "NASDAQ", "US10Y",
    "USDJPY", "KOSPI", "TAIEX",
]
CRYPTO_DB1 = ["BTCUSDT", "ETHUSDT"]
CRYPTO_DB2 = ["SOLUSDT", "BNBUSDT"]

DB1_URL = (os.getenv("ARGUS_DB_URL") or "").strip()
_DB1_CONN = None


def _db1_conn():
    global _DB1_CONN
    if _DB1_CONN is None or _DB1_CONN.closed:
        import psycopg
        _DB1_CONN = psycopg.connect(
            DB1_URL, connect_timeout=15,
        )
        log.info("🔌 DB1: соединение открыто (read)")
    return _DB1_CONN


def load_asia():
    sql = (
        "SELECT symbol, timestamp, change_pct "
        "FROM asia_market "
        "WHERE timestamp > NOW() - "
        "INTERVAL '1 day' * %s "
        "ORDER BY symbol, timestamp"
    )
    grouped = {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (WINDOW_DAYS,))
            for sym, ts, ch in cur.fetchall():
                if ch is None:
                    continue
                grouped.setdefault(sym, []).append(
                    (ts, float(ch)),
                )

    out = {}
    skipped = 0
    for sym, rows in grouped.items():
        clean = {}
        prev_ts = None
        for ts, ch in rows:
            if prev_ts is not None:
                dt_h = (
                    ts - prev_ts
                ).total_seconds() / 3600
                if dt_h > GAP_HOURS:
                    prev_ts = ts
                    skipped += 1
                    continue
            clean[ts] = ch
            prev_ts = ts
        out[sym] = clean

    log.info("gap points dropped: %d", skipped)
    return out


def load_candles_db2():
    sql = (
        "SELECT symbol, timestamp, close, open "
        "FROM candles "
        "WHERE timeframe = '1h' "
        "AND timestamp > NOW() - "
        "INTERVAL '1 day' * %s "
        "ORDER BY timestamp"
    )
    out = {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (WINDOW_DAYS,))
            for sym, ts, close, open_ in cur.fetchall():
                if close is None or open_ is None:
                    continue
                if float(open_) <= 0:
                    continue
                ch = (
                    (float(close) - float(open_))
                    / float(open_) * 100
                )
                out.setdefault(sym, {})[ts] = ch
    return out


def load_candles_db1():
    if not DB1_URL:
        log.warning("DB1 URL не задан")
        return {}
    sql = (
        "SELECT symbol, timestamp, "
        "change_pct FROM features_hourly "
        "WHERE symbol = ANY(%s) "
        "AND timestamp > NOW() - "
        "INTERVAL '1 day' * %s "
        "ORDER BY timestamp"
    )
    out = {}
    try:
        conn = _db1_conn()
        with conn.cursor() as cur:
            cur.execute(sql, (CRYPTO_DB1, WINDOW_DAYS))
            for sym, ts, ch in cur.fetchall():
                if ch is None:
                    continue
                out.setdefault(sym, {})[ts] = float(ch)
    except Exception as e:
        log.error("DB1 load: %s", e)
    return out


def pearson(xs, ys):
    if len(xs) < 2:
        return None
    try:
        a = np.array(xs, dtype=float)
        b = np.array(ys, dtype=float)
        if a.std() == 0 or b.std() == 0:
            return None
        c = np.corrcoef(a, b)[0, 1]
        if np.isnan(c):
            return None
        return round(float(c), 4)
    except Exception:
        return None


def save_vector(src, tgt, lag, corr, impact, n):
    sql = (
        "INSERT INTO impact_vectors "
        "(source_symbol, target_symbol, lag_hours, "
        "corr, impact_pct, samples, window_days) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (source_symbol, target_symbol, "
        "lag_hours, window_days) "
        "DO UPDATE SET "
        "corr = EXCLUDED.corr, "
        "impact_pct = EXCLUDED.impact_pct, "
        "samples = EXCLUDED.samples, "
        "computed_at = NOW()"
    )
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (
                src, tgt, lag, corr,
                impact, n, WINDOW_DAYS,
            ))


def save_pattern(src, tgt, cond, direction,
                 lag, n, hit, avg):
    sql = (
        "INSERT INTO asia_patterns "
        "(source_symbol, target_symbol, condition_pct, "
        "direction, lag_hours, samples, hit_rate, "
        "avg_impact_pct, window_days) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (source_symbol, target_symbol, "
        "condition_pct, direction, lag_hours) "
        "DO UPDATE SET "
        "samples = EXCLUDED.samples, "
        "hit_rate = EXCLUDED.hit_rate, "
        "avg_impact_pct = EXCLUDED.avg_impact_pct, "
        "window_days = EXCLUDED.window_days, "
        "computed_at = NOW()"
    )
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (
                src, tgt, cond, direction, lag,
                n, hit, avg, WINDOW_DAYS,
            ))


def log_run(job, status, n=0, err=None):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO collect_log "
                    "(job_name, metric, started_at, "
                    "finished_at, records_added, "
                    "status, error) "
                    "VALUES (%s,%s,%s,NOW(),%s,%s,%s)",
                    (
                        job, None,
                        datetime.now(timezone.utc),
                        n, status, err,
                    ),
                )
    except Exception as e:
        log.warning("log_run: %s", e)


def nearest(target_dt, sorted_list, tol_sec=TOL_SEC):
    if not sorted_list:
        return None
    i = bisect.bisect_left(sorted_list, target_dt)
    candidates = []
    if i < len(sorted_list):
        candidates.append(sorted_list[i])
    if i > 0:
        candidates.append(sorted_list[i - 1])
    best = None
    best_d = None
    for c in candidates:
        d = abs((c - target_dt).total_seconds())
        if d <= tol_sec:
            if best_d is None or d < best_d:
                best = c
                best_d = d
    return best


def build_sorted(seq_dict):
    return sorted(seq_dict.keys()), seq_dict


def latest_close(seq_dict):
    if not seq_dict:
        return None
    ts = sorted(seq_dict.keys())[-1]
    return seq_dict[ts]


def calc_corr_and_impact(asia_seq, crypto_seq):
    asia_ts, asia_d = build_sorted(asia_seq)
    crypto_ts, crypto_d = build_sorted(crypto_seq)
    if not asia_ts or not crypto_ts:
        return {}, {}

    corr_by_lag = {}
    impact_by_lag = {}

    for lag in LAGS:
        xs, ys = [], []
        for t in asia_ts:
            target = t + timedelta(hours=lag)
            ts2 = nearest(target, crypto_ts)
            if ts2 is None:
                continue
            xs.append(asia_d[t])
            ys.append(crypto_d[ts2])
        if not xs:
            continue
        if len(xs) < MIN_CORR_SAMPLES:
            continue
        c = pearson(xs, ys)
        corr_by_lag[lag] = c
        impact_by_lag[lag] = round(
            float(np.mean(ys)), 4,
        )
    return corr_by_lag, impact_by_lag


def calc_conditional(asia_seq, crypto_seq):
    asia_ts, asia_d = build_sorted(asia_seq)
    crypto_ts, crypto_d = build_sorted(crypto_seq)
    out = []
    for thr in THRESHOLDS:
        for direction in ("up", "down"):
            for lag in LAGS:
                impacts = []
                for t in asia_ts:
                    a = asia_d[t]
                    if direction == "up" and a <= thr:
                        continue
                    if direction == "down" and a >= -thr:
                        continue
                    target = t + timedelta(hours=lag)
                    ts2 = nearest(target, crypto_ts)
                    if ts2 is None:
                        continue
                    impacts.append(crypto_d[ts2])
                n = len(impacts)
                if n < MIN_SAMPLES:
                    continue
                avg = float(np.mean(impacts))
                if direction == "up":
                    hit = sum(
                        1 for x in impacts if x > 0
                    ) / n
                else:
                    hit = sum(
                        1 for x in impacts if x < 0
                    ) / n
                out.append((
                    thr, direction, lag, n,
                    round(hit, 4), round(avg, 4),
                ))
    return out


def process_pair(a_sym, c_sym, asia, crypto):
    log.info("%s -> %s", a_sym, c_sym)
    total_v = 0
    total_p = 0

    last_pct = latest_close(crypto[c_sym])

    corr, imp = calc_corr_and_impact(
        asia[a_sym], crypto[c_sym],
    )
    n = len(asia[a_sym])
    for lag in LAGS:
        if lag not in corr or corr[lag] is None:
            continue
        save_vector(
            a_sym, c_sym, lag,
            corr[lag], imp[lag], n,
        )
        total_v += 1
        log.info(
            "  lag=%dh corr=%s impact=%.2f%%",
            lag, corr[lag], imp[lag],
        )

    pats = calc_conditional(
        asia[a_sym], crypto[c_sym],
    )
    for (thr, dr, lag, n2, hit, avg) in pats:
        save_pattern(
            a_sym, c_sym, thr, dr,
            lag, n2, hit, avg,
        )
        total_p += 1
        log.info(
            "  %s %s>%.0f%% lag=%dh "
            "N=%d hit=%.2f avg=%.2f%%",
            a_sym, dr, thr, lag,
            n2, hit, avg,
        )
    return total_v, total_p


def main():
    log.info("=" * 60)
    log.info("ARGUS PATTERNS v2.7")
    log.info("window=%d, lags=%s", WINDOW_DAYS, LAGS)
    log.info(
        "min_samples=%d, min_corr_n=%d, tol=%ds",
        MIN_SAMPLES, MIN_CORR_SAMPLES, TOL_SEC,
    )
    log.info("gap_hours=%.1f", GAP_HOURS)
    log.info("=" * 60)

    try:
        asia = load_asia()
        crypto_db2 = load_candles_db2()
        crypto_db1 = load_candles_db1()
    except Exception as e:
        log.error("load: %s", e)
        log_run("collect_patterns", "fail", 0, str(e))
        close_connection()
        return

    crypto = {}
    crypto.update(crypto_db1)
    crypto.update(crypto_db2)

    log.info("markets: %s",
             {k: len(v) for k, v in asia.items()})
    log.info("crypto(DB1): %s",
             {k: len(v) for k, v in crypto_db1.items()})
    log.info("crypto(DB2): %s",
             {k: len(v) for k, v in crypto_db2.items()})

    total_v = 0
    total_p = 0
    targets = CRYPTO_DB1 + CRYPTO_DB2

    for a_sym in ASIA_SYMBOLS:
        if a_sym not in asia:
            continue
        for c_sym in targets:
            if c_sym not in crypto:
                continue
            try:
                v, p = process_pair(
                    a_sym, c_sym, asia, crypto,
                )
                total_v += v
                total_p += p
            except Exception as e:
                log.warning("%s->%s: %s",
                            a_sym, c_sym, e)

    log.info("=" * 60)
    log.info("DONE. vectors=%d patterns=%d",
             total_v, total_p)
    log.info("=" * 60)

    log_run(
        "collect_patterns", "ok",
        total_v + total_p,
    )

    if _DB1_CONN and not _DB1_CONN.closed:
        _DB1_CONN.close()
        log.info("🔌 DB1: соединение закрыто")
    close_connection()


if __name__ == "__main__":
    main()