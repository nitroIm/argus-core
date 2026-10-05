# ============================================================
# ARGUS-Trader - DATASET v10
# ------------------------------------------------------------
# v10: 64 features from 13 tables (DB1 + DB2).
#      Fixes: no long lines, no weird indents.
# ============================================================

import os
import sys
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
    except Exception as exc:
        print("DB2 fail: " + str(exc))

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
        os.getenv("DB2_SYMBOLS")
        or "SOLUSDT,BNBUSDT"
    ).split(",")
    if s.strip()
}

DATA_DIR = CRYPTO_ROOT / "data"


def symbol_conn(symbol):
    if symbol in DB2_SYMBOLS and DB2_OK:
        try:
            return get_conn_db2()
        except Exception as exc:
            log.warning(
                "db2 conn %s: %s", symbol, exc
            )
    return get_connection()


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

_CROSS_ALL = [
    "ref_change_1h",
    "ref_change_4h",
    "ref_change_24h",
    "ratio",
    "ratio_zscore_24h",
    "lead_lag_corr_24h",
    "spread_pct",
]

CROSS_COLS = _CROSS_ALL if USE_CROSS else []

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
                ts = ts.replace(
                    tzinfo=timezone.utc
                )
            out.append((ts, r[1:]))
        return out
    except Exception as exc:
        log.warning("fetch: %s", exc)
        return []


def _sql_sel(cols, table, where):
    return (
        "SELECT "
        + ", ".join(cols)
        + " FROM "
        + table
        + " WHERE "
        + where
    )


def fetch_features(symbol, limit=100000):
    cols = ["timestamp"] + INTERNAL_COLS
    sql = _sql_sel(
        cols,
        "features_hourly",
        "symbol = %s ORDER BY timestamp LIMIT %s",
    )
    try:
        with symbol_conn(symbol) as conn:
            return _fetch(conn, sql, (symbol, limit))
    except Exception as exc:
        log.warning("features %s: %s", symbol, exc)
        return []


def fetch_candles(symbol, limit=100000):
    cols = [
        "timestamp",
        "open",
        "high",
        "low",
        "close",
    ]
    sql = _sql_sel(
        cols,
        "candles",
        "symbol = %s AND timeframe = '1h' "
        "ORDER BY timestamp LIMIT %s",
    )
    try:
        with symbol_conn(symbol) as conn:
            return _fetch(conn, sql, (symbol, limit))
    except Exception as exc:
        log.warning("candles %s: %s", symbol, exc)
        return []


def fetch_oi(symbol):
    cols = ["timestamp", "oi", "oi_value"]
    sql = _sql_sel(
        cols,
        "open_interest",
        "symbol = %s AND oi IS NOT NULL "
        "ORDER BY timestamp",
    )
    try:
        with symbol_conn(symbol) as conn:
            return _fetch(conn, sql, (symbol,))
    except Exception as exc:
        log.warning("oi %s: %s", symbol, exc)
        return []


def fetch_ls(symbol):
    cols = ["timestamp", "ls_ratio"]
    sql = _sql_sel(
        cols,
        "long_short_ratio",
        "symbol = %s AND ls_ratio IS NOT NULL "
        "ORDER BY timestamp",
    )
    try:
        with symbol_conn(symbol) as conn:
            return _fetch(conn, sql, (symbol,))
    except Exception as exc:
        log.warning("ls %s: %s", symbol, exc)
        return []


def fetch_taker(symbol):
    cols = ["timestamp", "buy_vol", "sell_vol"]
    sql = _sql_sel(
        cols,
        "taker_flow",
        "symbol = %s AND buy_vol IS NOT NULL "
        "ORDER BY timestamp",
    )
    try:
        with symbol_conn(symbol) as conn:
            return _fetch(conn, sql, (symbol,))
    except Exception as exc:
        log.warning("taker %s: %s", symbol, exc)
        return []


def fetch_macro():
    cols = ["timestamp", "close"]
    sql = _sql_sel(
        cols,
        "macro_metrics",
        "symbol = 'US10Y' AND close IS NOT NULL "
        "ORDER BY timestamp",
    )
    try:
        with get_connection() as conn:
            return _fetch(conn, sql)
    except Exception as exc:
        log.warning("macro: %s", exc)
        return []


def fetch_onchain():
    cols = ["timestamp", "hashrate"]
    sql = _sql_sel(
        cols,
        "onchain_metrics",
        "symbol = 'BTC' AND hashrate IS NOT NULL "
        "ORDER BY timestamp",
    )
    try:
        with get_connection() as conn:
            return _fetch(conn, sql)
    except Exception as exc:
        log.warning("onchain: %s", exc)
        return []


def fetch_orderbook(symbol):
    cols = [
        "timestamp",
        "bid_pct",
        "ask_pct",
        "spread_pct",
    ]
    sql = _sql_sel(
        cols,
        "orderbook_snapshots",
        "symbol = %s AND bid_pct IS NOT NULL "
        "ORDER BY timestamp",
    )
    try:
        with symbol_conn(symbol) as conn:
            return _fetch(conn, sql, (symbol,))
    except Exception as exc:
        log.warning("ob %s: %s", symbol, exc)
        return []


def fetch_events(symbol):
    cols = ["timestamp", "event_type"]
    sql = _sql_sel(
        cols,
        "events",
        "symbol = %s ORDER BY timestamp",
    )
    try:
        with symbol_conn(symbol) as conn:
            return _fetch(conn, sql, (symbol,))
    except Exception as exc:
        log.warning("events %s: %s", symbol, exc)
        return []


def fetch_anomaly(symbol):
    cols = ["timestamp", "anomaly_type"]
    sql = _sql_sel(
        cols,
        "anomaly_log",
        "symbol = %s ORDER BY timestamp",
    )
    try:
        with symbol_conn(symbol) as conn:
            return _fetch(conn, sql, (symbol,))
    except Exception as exc:
        log.warning("anom %s: %s", symbol, exc)
        return []


def fetch_asia_market():
    if not DB2_OK:
        return {}
    try:
        with get_conn_db2() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT symbol, timestamp, "
                    "change_pct FROM asia_market "
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
                    out.setdefault(
                        sym, []
                    ).append((ts, float(ch)))
                return out
    except Exception as exc:
        log.warning("asia: %s", exc)
        return {}


def fetch_external(symbol):
    cols = ["timestamp", "change_pct"]
    sql = _sql_sel(
        cols,
        "external_market",
        "symbol = %s AND change_pct IS NOT NULL "
        "ORDER BY timestamp",
    )
    try:
        with get_connection() as conn:
            return _fetch(conn, sql, (symbol,))
    except Exception as exc:
        log.warning("ext %s: %s", symbol, exc)
        return []


def asof(series, ts, max_age_h):
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
    if ts - best[0] > timedelta(
        hours=max_age_h
    ):
        return None
    return best[1]


def asof_shift(series, ts, shift_h, max_age_h):
    return asof(
        series,
        ts - timedelta(hours=shift_h),
        max_age_h,
    )


def asia_at(asia_list, ts, lag_hours):
    if not asia_list:
        return None
    target = ts - timedelta(hours=lag_hours)
    best_val = None
    best_diff = None
    for a_ts, a_val in asia_list:
        diff = abs(
            (a_ts - target).total_seconds()
        )
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


def _safe(v):
    if v is None:
        return np.nan
    try:
        return float(v)
    except Exception:
        return np.nan


def _oi_feats(oi_series, ts):
    now = asof(oi_series, ts, MAX_AGE["oi"])
    p1 = asof_shift(
        oi_series, ts, 1, MAX_AGE["oi"]
    )
    p24 = asof_shift(
        oi_series, ts, 24, MAX_AGE["oi"]
    )
    c1 = np.nan
    c24 = np.nan
    if now is not None and p1 is not None:
        if p1[0] > 0:
            c1 = (now[0] - p1[0]) / p1[0] * 100
    if now is not None and p24 is not None:
        if p24[0] > 0:
            c24 = (
                (now[0] - p24[0]) / p24[0] * 100
            )
    return [_safe(c1), _safe(c24)]


def _ls_feats(ls_series, ts):
    now = asof(ls_series, ts, MAX_AGE["ls"])
    p1 = asof_shift(
        ls_series, ts, 1, MAX_AGE["ls"]
    )
    ch = np.nan
    if now is not None and p1 is not None:
        ch = now[0] - p1[0]
    v = now[0] if now else None
    return [_safe(v), _safe(ch)]


def _taker_feats(tk_series, ts):
    now = asof(tk_series, ts, MAX_AGE["taker"])
    p1 = asof_shift(
        tk_series, ts, 1, MAX_AGE["taker"]
    )
    bp = np.nan
    ch = np.nan
    if now is not None:
        bv, sv = now
        if bv + sv > 0:
            bp = bv / (bv + sv) * 100
    if now is not None and p1 is not None:
        bv, sv = now
        pbv, psv = p1
        if bv + sv > 0 and pbv + psv > 0:
            cur = bv / (bv + sv)
            prev = pbv / (pbv + psv)
            ch = (cur - prev) * 100
    return [_safe(bp), _safe(ch)]


def _macro_feats(series, ts):
    now = asof(series, ts, MAX_AGE["macro"])
    p = asof_shift(
        series, ts, 24, MAX_AGE["macro"]
    )
    lvl = np.nan
    ch = np.nan
    if now is not None:
        lvl = now[0]
    if now is not None and p is not None:
        if p[0] != 0:
            ch = (
                (now[0] - p[0])
                / abs(p[0])
                * 100
            )
    return [_safe(lvl), _safe(ch)]


def _onchain_feats(series, ts):
    now = asof(
        series, ts, MAX_AGE["onchain"]
    )
    p = asof_shift(
        series, ts, 24, MAX_AGE["onchain"]
    )
    lh = np.nan
    ch = np.nan
    if now is not None and now[0] > 0:
        lh = float(np.log(now[0]))
    if now is not None and p is not None:
        if p[0] > 0:
            ch = (now[0] - p[0]) / p[0] * 100
    return [_safe(lh), _safe(ch)]


def _ob_feats(series, ts):
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


def _count_type(series, ts, hours, etype):
    if not series:
        return 0
    cutoff = ts - timedelta(hours=hours)
    n = 0
    for e_ts, et in series:
        if e_ts > cutoff and e_ts <= ts:
            if et == etype:
                n += 1
    return n


def _event_feats(series, ts):
    return [
        _count_events(series, ts, 1),
        _count_events(series, ts, 24),
        _count_type(
            series, ts, 24, "rsi_overbought"
        ),
    ]


def _anom_feats(series, ts):
    return [_count_events(series, ts, 24)]


def _asia_feats(asia, ts):
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


def _ext_feats(dxy, spx, gold, ts):
    v1 = asof(dxy, ts, 3)
    v2 = asof(spx, ts, 3)
    v3 = asof(gold, ts, 3)
    return [
        _safe(v1[0]) if v1 else np.nan,
        _safe(v2[0]) if v2 else np.nan,
        _safe(v3[0]) if v3 else np.nan,
    ]


def build_targets(candles, horizon):
    out = {}
    n = len(candles)
    for i in range(n):
        ts = candles[i][0]
        if i + horizon >= n:
            out[ts] = None
            continue
        c0 = candles[i][1][3]
        c1 = candles[i + horizon][1][3]
        if not c0 or not c1 or c0 <= 0:
            out[ts] = None
            continue
        out[ts] = (c1 - c0) / c0 * 100
    return out


def _row_for(symbol, d, asia, ts,
             ext_dxy, ext_spx, ext_gold):
    row = []
    for i in range(len(INTERNAL_COLS)):
        v = d["fa"][i].get(ts)
        row.append(v if v is not None else np.nan)
    row.extend(_oi_feats(d["oi"], ts))
    row.extend(_ls_feats(d["ls"], ts))
    row.extend(_taker_feats(d["taker"], ts))
    row.extend(_macro_feats(d["macro"], ts))
    row.extend(_onchain_feats(d["onchain"], ts))
    row.extend(_ob_feats(d["ob"], ts))
    row.extend(_event_feats(d["events"], ts))
    row.extend(_anom_feats(d["anom"], ts))
    row.extend(_asia_feats(asia, ts))
    if USE_EXTERNAL:
        row.extend(_ext_feats(
            ext_dxy, ext_spx, ext_gold, ts
        ))
    return row


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
        targets = targets_map.get(symbol) or {}

        for ts in sorted(d["feats"]):
            ret = targets.get(ts)
            if ret is None:
                continue
            row = _row_for(
                symbol, d, asia, ts,
                ext_dxy, ext_spx, ext_gold,
            )
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


def _build_feat_arrays(rows):
    feats = {}
    fa = [dict() for _ in INTERNAL_COLS]
    for ts, vals in rows:
        feats[ts] = True
        for i, v in enumerate(vals):
            if v is None:
                continue
            try:
                fa[i][ts] = float(v)
            except Exception:
                pass
    return feats, fa


def prepare(test_frac=0.2):
    log.info("=" * 60)
    log.info("DATASET v10 (all features)")
    log.info("SYMBOLS=%s", SYMBOLS)
    log.info("HORIZON=%dh", HORIZON)
    log.info("DB2_OK=%s", DB2_OK)
    log.info(
        "features expected: %d",
        len(FEATURE_COLS),
    )
    log.info("=" * 60)

    asia = fetch_asia_market()
    log.info(
        "asia_market: %d symbols", len(asia)
    )

    ext_dxy = (
        fetch_external("DXY")
        if USE_EXTERNAL else []
    )
    ext_spx = (
        fetch_external("SPX")
        if USE_EXTERNAL else []
    )
    ext_gold = (
        fetch_external("GOLD")
        if USE_EXTERNAL else []
    )

    macro = fetch_macro()
    log.info("macro points: %d", len(macro))

    onchain = fetch_onchain()
    log.info("onchain: %d", len(onchain))

    symbols_data = {}
    targets_map = {}

    for symbol in SYMBOLS:
        rows = fetch_features(symbol)
        if not rows:
            log.warning(
                "%s: no features (skip)", symbol
            )
            continue

        feats, fa = _build_feat_arrays(rows)

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
        anom = fetch_anomaly(symbol)

        symbols_data[symbol] = {
            "feats": feats,
            "fa": fa,
            "oi": oi,
            "ls": ls,
            "taker": taker,
            "macro": macro,
            "onchain": onchain,
            "ob": ob,
            "events": events,
            "anom": anom,
        }

        log.info(
            "%s: feat=%d candles=%d "
            "oi=%d ls=%d taker=%d "
            "ob=%d ev=%d an=%d",
            symbol, len(feats), len(candles),
            len(oi), len(ls), len(taker),
            len(ob), len(events), len(anom),
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