# ============================================================
# ARGUS-Trader - DATASET v10 [PRODUCTION]
# ------------------------------------------------------------
# v10: pulls ALL available tables from DB1+DB2.
#      Feature buckets: internal, oi, ls, taker, macro,
#      onchain, orderbook, events, anomaly, asia, cross.
#      As-of joins with forward-fill. ~70 features.
# v8.2: regression target, 30 features.
# ============================================================

import os
import sys
import json
import logging
from pathlib import Path
from datetime import (
    datetime, timezone, timedelta,
)

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
log = logging.getLogger("crypto.learn.dataset")

USE_EXTERNAL = (
    os.getenv("USE_EXTERNAL", "0").strip() == "1"
)
USE_CROSS = (
    os.getenv("USE_CROSS", "0").strip() == "1"
)

HORIZON = int(os.getenv("HORIZON", "12"))

MOVE_THRESHOLD_PCT = float(
    os.getenv("MOVE_THRESHOLD_PCT", "0.5")
)

DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT"]

SYMBOLS = [
    s.strip().upper()
    for s in (
        os.getenv("SYMBOLS")
        or ",".join(DEFAULT_SYMBOLS)
    ).split(",")
    if s.strip()
]

REFERENCE = (
    os.getenv("REFERENCE") or "BTCUSDT"
).strip().upper()

DB2_SYMBOLS = {
    s.strip().upper()
    for s in (
        os.getenv("DB2_SYMBOLS") or "SOLUSDT,BNBUSDT"
    ).split(",")
    if s.strip()
}

DATA_DIR = CRYPTO_ROOT / "data"

# As-of max age (hours). Older -> NaN.
MAX_AGE = {
    "funding": 24,
    "oi": 4,
    "ls": 4,
    "taker": 4,
    "macro": 12,
    "onchain": 72,
    "orderbook": 4,
    "asia": 2,
    "events": 168,
    "anomaly": 48,
}


def symbol_conn(symbol):
    if symbol in DB2_SYMBOLS and DB2_OK:
        try:
            return get_conn_db2()
        except Exception as e:
            log.warning("db2 conn %s: %s", symbol, e)
    return get_connection()


# ============================================================
# Feature buckets
# ============================================================
INTERNAL_COLS = [
    "change_pct",
    "range_pct",
    "body_pct",
    "upper_wick_pct",
    "lower_wick_pct",
    "volume_ratio_24h",
    "volatility_24h",
    "volatility_7d",
    "change_4h",
    "change_24h",
    "change_7d",
    "change_1d",
    "change_3d",
    "trend_up",
    "hour_of_day",
    "day_of_week",
    "funding_rate",
    "funding_trend",
    "ema9_dist_pct",
    "ema21_dist_pct",
    "ema50_dist_pct",
    "macd",
    "macd_signal",
    "bb_upper_dist",
    "bb_lower_dist",
    "bb_width_pct",
    "dist_high_24h_pct",
    "dist_low_24h_pct",
    "consecutive_up",
    "session",
]

OI_COLS = [
    "oi_change_1h",
    "oi_change_24h",
]

LS_COLS = [
    "ls_ratio_asof",
    "ls_change_1h",
]

TAKER_COLS = [
    "taker_buy_pct",
    "taker_change_1h",
]

MACRO_COLS = [
    "us10y_level",
    "us10y_change_1d",
]

ONCHAIN_COLS = [
    "hashrate_log",
    "hashrate_change_1d",
]

OB_COLS = [
    "ob_bid_pct",
    "ob_ask_pct",
    "ob_spread_pct",
]

EVENT_COLS = [
    "events_1h",
    "events_24h",
    "rsi_overbought_24h",
]

ANOMALY_COLS = [
    "anomaly_24h",
]

ASIA_COLS = [
    "asia_nikkei_6h",
    "asia_shanghai_6h",
    "asia_shanghai_12h",
    "asia_hangseng_6h",
    "asia_usdcny_6h",
    "asia_usdcny_12h",
    "asia_dax_6h",
    "asia_stoxx50_6h",
    "asia_ftse_6h",
    "asia_eurusd_6h",
    "asia_vix_1h",
    "asia_nasdaq_1h",
    "asia_us10y_6h",
    "asia_usdjpy_6h",
    "asia_kospi_6h",
    "asia_taiex_6h",
    "asia_impact_score",
]

EXTERNAL_COLS = [
    "dxy_change_pct",
    "spx_change_pct",
    "gold_change_pct",
]

CROSS_COLS = [
    "ref_change_1h",
    "ref_change_4h",
    "ref_change_24h",
    "ratio",
    "ratio_zscore_24h",
    "lead_lag_corr_24h",
    "spread_pct",
] if USE_CROSS else []

FEATURE_COLS = (
    INTERNAL_COLS
    + OI_COLS
    + LS_COLS
    + TAKER_COLS
    + MACRO_COLS
    + ONCHAIN_COLS
    + OB_COLS
    + EVENT_COLS
    + ANOMALY_COLS
    + ASIA_COLS
    + (EXTERNAL_COLS if USE_EXTERNAL else [])
    + CROSS_COLS
)

TARGET_RET = "next_return"
TARGET_COL = "next_change_pct"


# ============================================================
# Generic DB fetch
# ============================================================
def _fetch(conn, sql, params=()):
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        out = []
        for r in rows:
            ts = r[0]
            if ts is None:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            out.append((ts, r[1:]))
        return out
    except Exception as e:
        log.warning("fetch: %s", e)
        return []


def fetch_features(symbol, limit=100000):
    base_cols = ["timestamp"] + INTERNAL_COLS
    try:
        with symbol_conn(symbol) as conn:
            return _fetch(
                conn,
                "SELECT " + ", ".join(base_cols)
                + " FROM features_hourly "
                + "WHERE symbol = %s "
                + "ORDER BY timestamp LIMIT %s",
                (symbol, limit),
            )
    except Exception as e:
        log.warning("features %s: %s", symbol, e)
        return []


def fetch_candles(symbol, limit=100000):
    try:
        with symbol_conn(symbol) as conn:
            return _fetch(
                conn,
                "SELECT timestamp, open, high, low, close "
                "FROM candles "
                "WHERE symbol = %s "
                "AND timeframe = '1h' "
                "ORDER BY timestamp LIMIT %s",
                (symbol, limit),
               )
    except Exception as e:
        log.warning(" except Exceptioncandles %s: as %s", symbol, e)
        return []

 e
def fetch_oi(symbol):
    try:
        with symbol_conn(symbol) as conn:
            return _fetch(
                conn,
                "SELECT timestamp, oi, oi_value "
                "FROM open_interest "
                "WHERE symbol = %s "
                "AND (oi IS NOT NULL OR oi_value IS NOT NULL) "
                "ORDER BY timestamp",
                (symbol,),
            )
    except Exception as e:
        log.warning("oi %s: %s", symbol, e)
        return []


def fetch_ls(symbol):
    try:
        with symbol_conn(symbol) as conn:
            return _fetch(
                conn,
                "SELECT timestamp, ls_ratio "
                "FROM long_short_ratio "
                "WHERE symbol = %s "
                "AND ls_ratio IS NOT NULL "
                "ORDER BY timestamp",
                (symbol,),
            )
    except Exception as e:
        log.warning("ls %s: %s", symbol, e)
        return []


def fetch_taker(symbol):
    try:
        with symbol_conn(symbol) as conn:
            return _fetch(
                conn,
                "SELECT timestamp, buy_vol, sell_vol "
                "FROM taker_flow "
                "WHERE symbol = %s "
                "AND buy_vol IS NOT NULL "
                "AND sell_vol IS NOT NULL "
                "ORDER BY timestamp",
                (symbol,),
            )
    except Exception as e:
        log.warning("taker %s: %s", symbol, e)
        return []


def fetch_macro():
    try:
        with get_connection() as conn:
            return _fetch(
                conn,
                "SELECT timestamp, close "
                "FROM macro_metrics "
                "WHERE symbol = 'US10Y' "
                "AND close IS NOT NULL "
                "ORDER BY timestamp",
            )
:
        log.warning("macro: %s", e)
        return []


def fetch_onchain():
    try:
        with get_connection() as conn:
            return _fetch(
                conn,
                "SELECT timestamp, hashrate "
                "FROM onchain_metrics "
                "WHERE symbol = 'BTC' "
                "AND hashrate IS NOT NULL "
                "ORDER BY timestamp",
            )
    except Exception as e:
        log.warning("onchain: %s", e)
        return []


def fetch_orderbook(symbol):
    try:
        with symbol_conn(symbol) as conn:
            return _fetch(
                conn,
                "SELECT timestamp, bid_pct, ask_pct, "
                "spread_pct FROM orderbook_snapshots "
                "WHERE symbol = %s "
                "AND bid_pct IS NOT NULL "
                "ORDER BY timestamp",
                (symbol,),
            )
    except Exception as e:
        log.warning("orderbook %s: %s", symbol, e)
        return []


def fetch_events(symbol):
    try:
        with symbol_conn(symbol) as conn:
            return _fetch(
                conn,
                "SELECT timestamp, event_type "
                "FROM events "
                "WHERE symbol = %s "
                "ORDER BY timestamp",
                (symbol,),
            )
    except Exception as e:
        log.warning("events %s: %s", symbol, e)
        return []


def fetch_anomaly(symbol):
    try:
        with symbol_conn(symbol) as conn:
            return _fetch(
                conn,
                "SELECT timestamp, anomaly_type "
                "FROM anomaly_log "
                "WHERE symbol = %s "
                "ORDER BY timestamp",
                (symbol,),
            )
    except Exception as e:
        log.warning("anomaly %s: %s", symbol, e)
        return []


def fetch_asia_market():
    if not DB2_OK:
        return {}
    try:
        with get_conn_db2() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT symbol, timestamp, change_pct "
                    "FROM asia_market "
                    "WHERE change_pct IS NOT NULL "
                    "ORDER BY symbol, timestamp"
                )
                out = {}
                for sym, ts, ch in cur.fetchall():
                    if ts is None:
                        continue
                    if ts.tzinfo is None:
                        ts = ts.replace(
                            tzinfo=timezone.utc
                        )
                    out.setdefault(sym, []).append(
                        (ts, float(ch))
                    )
                return out
    except Exception as e:
        log.warning("asia_market: %s", e)
        return {}


def fetch_external(symbol):
    try:
        with get_connection() as conn:
            return _fetch(
                conn,
                "SELECT timestamp, change_pct "
                "FROM external_market "
                "WHERE symbol = %s "
                "AND change_pct IS NOT NULL "
                "ORDER BY timestamp",
                (symbol,),
            )
    except Exception as e:
        log.warning("external %s: %s", symbol, e)
        return []


# ============================================================
# As-of joins (forward fill, with max age)
# ============================================================
def asof(series, ts, max_age_h):
    """Return last value at or before ts, else None."""
    if not series:
        return None
    best = None
    for s_ts, s_val in series:
        if s_ts <= ts:
            best = (s_ts, s_val)
        else:
            break
    if best is None:
        return None
    if ts - best[0] > timedelta(hours=max_age_h):
        return None
    return best[1]


def asof_shift(series, ts, shift_h, max_age_h):
    return asof(series, ts - timedelta(hours=shift_h),
                max_age_h)


def asia_at(asia_list, ts, lag_hours):
    if not asia_list:
        return None
    target = ts - timedelta(hours=lag_hours)
    best_val = None
    best_diff = None
    for a_ts, a_val in asia_list:
        diff = abs((a_ts - target).total_seconds())
        if diff <= 1800:
            if best_diff is None or diff < best_diff:
                best_val = a_val
                best_diff = diff
    return best_val


def asia_impact_score(asia, ts):
    parts = []
    sh = asia_at(asia.get("SHANGHAI", []), ts, 6)
    if sh is not None:
        parts.append(-sh)
    for src in ["DAX", "NASDAQ", "NIKKEI"]:
        v = asia_at(asia.get(src, []), ts, 6)
        if v is not None:
            parts.append(v)
    v = asia_at(asia.get("VIX", []), ts, 1)
    if v is not None:
        parts.append(-v)
    if not parts:
        return None
    return round(sum(parts), 4)


# ============================================================
# Feature construction
# ============================================================
def _safe(v):
    if v is None:
        return np.nan
    try:
        return float(v)
    except Exception:
        return np.nan


def _build_oi_feats(oi_series, ts):
    """oi_change_1h and oi_change_24h from as-of OI."""
    now = asof(oi_series, ts, MAX_AGE["oi"])
    prev1 = asof_shift(oi_series, ts, 1, MAX_AGE["oi"])
    prev24 = asof_shift(oi_series, ts, 24, MAX_AGE["oi"])

    c1 = np.nan
    c24 = np.nan
    if now is not None and prev1 is not None:
        if prev1[0] > 0:
            c1 = (now[0] - prev1[0]) / prev1[0] * 100
    if now is not None and prev24 is not None:
        if prev24[0] > 0:
            c24 = (now[0] - prev24[0]) / prev24[0] * 100
    return [_safe(c1), _safe(c24)]


def _build_ls_feats(ls_series, ts):
    now = asof(ls_series, ts, MAX_AGE["ls"])
    prev1 = asof_shift(ls_series, ts, 1, MAX_AGE["ls"])
    ch = np.nan
    if now is not None and prev1 is not None:
        ch = now[0] - prev1[0]
    return [_safe(now[0]) if now else np.nan,
            _safe(ch)]


def _build_taker_feats(tk_series, ts):
    now = asof(tk_series, ts, MAX_AGE["taker"])
    prev1 = asof_shift(tk_series, ts, 1, MAX_AGE["taker"])
    buy_pct = np.nan
    ch = np.nan
    if now is not None:
        bv, sv = now
        if bv + sv > 0:
            buy_pct = bv / (bv + sv) * 100
    if now is not None and prev1 is not None:
        bv, sv = now
        pbv, psv = prev1
        if bv + sv > 0 and pbv + psv > 0:
            cur = bv / (bv + sv)
            prev = pbv / (pbv + psv)
            ch = (cur - prev) * 100
    return [_safe(buy_pct), _safe(ch)]


def _build_macro_feats(series, ts):
    now = asof(series, ts, MAX_AGE["macro"])
    prev = asof_shift(series, ts, 24,
                      MAX_AGE["macro"])
    lvl = np.nan
    ch = np.nan
    if now is not None:
        lvl = now[0]
    if now is not None and prev is not None:
        if prev[0] != 0:
            ch = (now[0] - prev[0]) / abs(
                prev[0]
            ) * 100
    return [_safe(lvl), _safe(ch)]


def _build_onchain_feats(series, ts):
    now = asof(series, ts, MAX_AGE["onchain"])
    prev = asof_shift(series, ts, 24,
                      MAX_AGE["onchain"])
    log_hr = np.nan
    ch = np.nan
    if now is not None and now[0] > 0:
        log_hr = float(np.log(now[0]))
    if now is not None and prev is not None:
        if prev[0] > 0:
            ch = (now[0] - prev[0]) / prev[0] * 100
    return [_safe(log_hr), _safe(ch)]


def _build_ob_feats(series, ts):
    now = asof(series, ts, MAX_AGE["orderbook"])
    if now is None:
        return [np.nan, np.nan, np.nan]
    return [_safe(now[0]), _safe(now[1]),
            _safe(now[2])]


def _count_events(series, ts, hours):
    if not series:
        return 0
    cutoff = ts - timedelta(hours=hours)
    n = 0
    for e_ts, _ in series:
        if e_ts > cutoff and e_ts <= ts:
            n += 1
    return n


def _count_events_type(series, ts, hours, etype):
    if not series:
        return 0
    cutoff = ts - timedelta(hours=hours)
    n = 0
    for e_ts, et in series:
        if e_ts > cutoff and e_ts <= ts and et == etype:
            n += 1
    return n


def _build_event_feats(series, ts):
    return [
        _count_events(series, ts, 1),
        _count_events(series, ts, 24),
        _count_events_type(
            series, ts, 24, "rsi_overbought"
        ),
    ]


def _build_anomaly_feats(series, ts):
    return [_count_events(series, ts, 24)]


def _build_asia_feats(asia, ts):
    return [
        asia_at(asia.get("NIKKEI", []), ts, 6),
        asia_at(asia.get("SHANGHAI", []), ts, 6),
        asia_at(asia.get("SHANGHAI", []), ts, 12),
        asia_at(asia.get("HANGSENG", []), ts, 6),
        asia_at(asia.get("USDCNY", []), ts, 6),
        asia_at(asia.get("USDCNY", []), ts, 12),
        asia_at(asia.get("DAX", []), ts, 6),
        asia_at(asia.get("SX5E", []), ts, 6),
        asia_at(asia.get("FTSE", []), ts, 6),
        asia_at(asia.get("EURUSD", []), ts, 6),
        asia_at(asia.get("VIX", []), ts, 1),
        asia_at(asia.get("NASDAQ", []), ts, 1),
        asia_at(asia.get("US10Y", []), ts, 6),
        asia_at(asia.get("USDJPY", []), ts, 6),
        asia_at(asia.get("KOSPI", []), ts, 6),
        asia_at(asia.get("TAIEX", []), ts, 6),
        asia_impact_score(asia, ts),
    ]


def _build_external_feats(ext_dxy, ext_spx,
                          ext_gold, ts):
    v1 = asof(ext_dxy, ts, 3)
    v2 = asof(ext_spx, ts, 3)
    v3 = asof(ext_gold, ts, 3)
    return [
        _safe(v1[0]) if v1 else np.nan,
        _safe(v2[0]) if v2 else np.nan,
        _safe(v3[0]) if v3 else np.nan,
    ]


# ============================================================
# Targets
# ============================================================
def build_targets(candles, horizon):
    out = {}
    n = len(candles)
    for i in range(n):
        if i + horizon >= n:
            out[candles[i][0]] = None
            continue
        c0 = candles[i][1][3]
        c1 = candles[i + horizon][1][3]
        if not c0 or not c1 or c0 <= 0:
            out[candles[i][0]] = None
            continue
        ret = (c1 - c0) / c0 * 100
        out[candles[i][0]] = ret
    return out


# ============================================================
# Build X, y
# ============================================================
def build_xy_all(
    symbols_data, targets_map, asia,
    ext_dxy, ext_spx, ext_gold,
):
    X, y_dir, y_ret = [], [], []
    ts_list, sym_list = [], []

    for symbol in SYMBOLS:
        d = symbols_data.get(symbol)
        if not d:
            continue
        feats = d["feats"]
        targets = targets_map.get(symbol) or {}

        for ts in sorted(feats.keys()):
            ret = targets.get(ts)
            if ret is None:
                continue
            row = []
            for i, col in enumerate(INTERNAL_COLS):
                row.append(
                    d["feat_arrays"][i].get(ts, np.nan)
                )
            row.extend(_build_oi_feats(
                d["oi"], ts,
            ))
            row.extend(_build_ls_feats(
                d["ls"], ts,
            ))
            row.extend(_build_taker_feats(
                d["taker"], ts,
            ))
            row.extend(_build_macro_feats(
                d["macro"], ts,
            ))
            row.extend(_build_onchain_feats(
                d["onchain"], ts,
            ))
            row.extend(_build_ob_feats(
                d["ob"], ts,
            ))
            row.extend(_build_event_feats(
                d["events"], ts,
            ))
            row.extend(_build_anomaly_feats(
                d["anomaly"], ts,
            ))
            row.extend(_build_asia_feats(asia, ts))
            if USE_EXTERNAL:
                row.extend(_build_external_feats(
                    ext_dxy, ext_spx,
                    ext_gold, ts,
                ))
            X.append(row)
            y_dir.append(1 if ret > 0 else 0)
            y_ret.append(float(ret))
            ts_list.append(ts)
            sym_list.append(symbol)

    if not X:
        return (
            np.array([], dtype=np.float32),
            np.array([], dtype=np.int32),
            np.array([], dtype=np.float32),
            [], [],
        )

    order = sorted(
        range(len(ts_list)),
        key=lambda i: ts_list[i],
    )
    X = [X[i] for i in order]
    y_dir = [y_dir[i] for i in order]
    y_ret = [y_ret[i] for i in order]
    ts_list = [ts_list[i] for i in order]
    sym_list = [sym_list[i] for i in order]

    return (
        np.array(X, dtype=np.float32),
        np.array(y_dir, dtype=np.int32),
        np.array(y_ret, dtype=np.float32),
        ts_list,
        sym_list,
    )


def per_symbol_split(
    X, y, y_ret, ts_list, sym_list,
    test_frac=0.2,
):
    X = np.asarray(X)
    y = np.asarray(y)
    y_ret = np.asarray(y_ret)

    train_idx, test_idx = [], []

    for sym in sorted(set(sym_list)):
        idxs = [
            i for i, s in enumerate(sym_list)
            if s == sym
        ]
        idxs_sorted = sorted(
            idxs, key=lambda i: ts_list[i]
        )
        n = len(idxs_sorted)
        if n < 20:
            train_idx.extend(idxs_sorted)
            continue
        split = int(n * (1 - test_frac))
        train_idx.extend(idxs_sorted[:split])
        test_idx.extend(idxs_sorted[split:])
        log.info(
            "  %s: train=%d test=%d",
            sym, split, n - split,
        )

    train_idx.sort(key=lambda i: ts_list[i])
    test_idx.sort(key=lambda i: ts_list[i])

    return (
        X[train_idx], y[train_idx],
        y_ret[train_idx],
        X[test_idx], y[test_idx],
        y_ret[test_idx],
    )


def prepare(test_frac=0.2):
    log.info("=" * 60)
    log.info("DATASET v10 (all features)")
    log.info("SYMBOLS=%s", SYMBOLS)
    log.info("HORIZON=%dh", HORIZON)
    log.info("DB2_OK=%s", DB2_OK)
    log.info(
        "features expected: %d", len(FEATURE_COLS),
    )
    log.info("=" * 60)

    asia = fetch_asia_market()
    log.info(
        "asia_market: %d symbols", len(asia)
    )

    ext_dxy = fetch_external("DXY") if USE_EXTERNAL else []
    ext_spx = fetch_external("SPX") if USE_EXTERNAL else []
    ext_gold = fetch_external("GOLD") if USE_EXTERNAL else []

    macro = fetch_macro()
    log.info("macro points: %d", len(macro))

    onchain = fetch_onchain()
    log.info("onchain points: %d", len(onchain))

    symbols_data = {}
    targets_map = {}

    for symbol in SYMBOLS:
        rows = fetch_features(symbol)
        if not rows:
            log.warning(
                "%s: no features (skip)", symbol
            )
            continue

        feats = {}
        feat_arrays = [dict() for _ in INTERNAL_COLS]
        for ts, vals in rows:
            feats[ts] = True
            for i, v in enumerate(vals):
                if v is None:
                    continue
                try:
                    feat_arrays[i][ts] = float(v)
                except Exception:
                    pass

        candles = fetch_candles(symbol)
        if not candles:
            log.warning(
                "%s: no candles (skip)", symbol
            )
            continue

        targets_map[symbol] = build_targets(
            candles, HORIZON,
        )

        oi = fetch_oi(symbol)
        ls = fetch_ls(symbol)
        taker = fetch_taker(symbol)
        ob = fetch_orderbook(symbol)
        events = fetch_events(symbol)
        anomaly = fetch_anomaly(symbol)

        symbols_data[symbol] = {
            "feats": feats,
            "feat_arrays": feat_arrays,
            "oi": [
                (ts, (v[0], v[1])) for ts, v in oi
            ],
            "ls": [
                (ts, (v[0],)) for ts, v in ls
            ],
            "taker": [
                (ts, (v[0], v[1])) for ts, v in taker
            ],
            "macro": [
                (ts, (v[0],)) for ts, v in macro
            ],
            "onchain": [
                (ts, (v[0],)) for ts, v in onchain
            ],
            "ob": [
                (ts, (v[0], v[1], v[2]))
                for ts, v in ob
            ],
            "events": [
                (ts, v[0]) for ts, v in events
            ],
            "anomaly": [
                (ts, v[0]) for ts, v in anomaly
            ],
        }

        log.info(
            "%s: feat=%d candles=%d oi=%d "
            "ls=%d taker=%d ob=%d ev=%d an=%d",
            symbol, len(feats), len(candles),
            len(oi), len(ls), len(taker),
            len(ob), len(events), len(anomaly),
        )

    if REFERENCE not in symbols_data:
        log.error("REFERENCE %s missing", REFERENCE)
        return None

    X, y_dir, y_ret, ts, sym = build_xy_all(
        symbols_data, targets_map, asia,
        ext_dxy, ext_spx, ext_gold,
    )

    log.info("samples: %d", len(X))

    if len(X) < 100:
        log.error("too few samples")
        return None

    log.info("per-symbol time split:")
    (
        X_train, y_train, r_train,
        X_test, y_test, r_test,
    ) = per_symbol_split(
        X, y_dir, y_ret, ts, sym, test_frac,
    )

    balance = {
        "up_train": int(y_train.sum()),
        "down_train": int(
            len(y_train) - y_train.sum()
        ),
        "ret_mean": float(r_train.mean()),
        "ret_std": float(r_train.std()),
    }

    log.info(
        "split: train=%d test=%d features=%d",
        len(X_train), len(X_test),
        len(FEATURE_COLS),
    )
    log.info(
        "ret_train: mean=%.4f std=%.4f",
        balance["ret_mean"],
        balance["ret_std"],
    )

    return {
        "X_train": X_train,
        "y_train": y_train,
        "X_test": X_test,
        "y_test": y_test,
        "r_train": r_train,
        "r_test": r_test,
        "n_total": len(X),
        "n_train": len(X_train),
        "n_test": len(X_test),
        "balance": balance,
        "feature_cols": FEATURE_COLS,
        "horizon": HORIZON,
        "symbols": sorted(set(sym)),
        "reference": REFERENCE,
    }


def main():
    data = prepare()
    if data is None:
        return
    log.info(
        "total=%d train=%d test=%d features=%d",
        data["n_total"],
        data["n_train"], data["n_test"],
        len(data["feature_cols"]),
    )


if __name__ == "__main__":
    main()