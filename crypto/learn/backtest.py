# ============================================================
# ARGUS-Trader - BACKTEST v3
# ------------------------------------------------------------
# v3: remove cat n_features_in_ check. CatBoost does not
#     restore that attribute on load_model -> false negative.
# v2: nan_to_num for ridge/mlp/cat.
# v1: initial.
# ============================================================

import os
os.environ.setdefault("USE_CROSS", "0")

import sys
import json
import logging
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import joblib
import lightgbm as lgb
import xgboost as xgb
from catboost import CatBoostRegressor

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

import dataset as ds

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.learn.backtest")

MODELS_DIR = SCRIPT_DIR / "models"

THRESHOLD_PCT = float(
    os.getenv("BT_THRESHOLD_PCT", "0.5")
)
MAX_ABS_PCT = float(
    os.getenv("BT_MAX_ABS_PCT", "15.0")
)
INITIAL_BALANCE = float(
    os.getenv("BT_BALANCE", "1000.0")
)
POSITION_NOTIONAL = float(
    os.getenv("BT_NOTIONAL", "20.0")
)
FEE_RATE = float(
    os.getenv("BT_FEE_RATE", "0.0014")
)
FUNDING_RATE = float(
    os.getenv("BT_FUNDING", "0.0003")
)
TEST_FRAC = float(
    os.getenv("BT_TEST_FRAC", "0.2")
)

SYMBOLS_LIST = [
    s.strip().upper()
    for s in (
        os.getenv("SYMBOLS")
        or "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT"
    ).split(",")
    if s.strip()
]
DB2_SET = {
    s.strip().upper()
    for s in (
        os.getenv("DB2_SYMBOLS")
        or "SOLUSDT,BNBUSDT"
    ).split(",")
    if s.strip()
}


def load_weights():
    p = MODELS_DIR / "ensemble_weights.json"
    if not p.exists():
        return {}
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def _sym_weights(wdata, sym):
    if not wdata:
        return {}, True
    entry = wdata.get("symbols", {}).get(sym)
    if not entry:
        return {}, True
    w = entry.get("weights") or {}
    allowed = entry.get("trade_allowed", True)
    return w, allowed


def _clean(X):
    return np.nan_to_num(
        X,
        nan=0.0,
        posinf=0.0,
        neginf=0.0,
    )


def predict_lgb(sym, X):
    mf = MODELS_DIR / ("lgb_" + sym + ".txt")
    if not mf.exists():
        return None
    try:
        m = lgb.Booster(model_file=str(mf))
        if m.num_feature() != X.shape[1]:
            log.warning(
                "lgb %s: expects %d got %d",
                sym, m.num_feature(), X.shape[1],
            )
            return None
        return m.predict(X).astype(np.float32)
    except Exception as exc:
        log.warning("lgb %s: %s", sym, exc)
        return None


def predict_xgb(sym, X):
    mf = MODELS_DIR / ("xgb_" + sym + ".json")
    if not mf.exists():
        return None
    try:
        m = xgb.Booster()
        m.load_model(str(mf))
        if m.num_features() != X.shape[1]:
            log.warning(
                "xgb %s: expects %d got %d",
                sym, m.num_features(), X.shape[1],
            )
            return None
        d = xgb.DMatrix(X)
        bi = getattr(m, "best_iteration", None)
        if bi is not None:
            return m.predict(
                d, iteration_range=(0, bi + 1),
            ).astype(np.float32)
        return m.predict(d).astype(np.float32)
    except Exception as exc:
        log.warning("xgb %s: %s", sym, exc)
        return None


def predict_cat(sym, X):
    mf = MODELS_DIR / ("cat_" + sym + ".cbm")
    if not mf.exists():
        return None
    try:
        m = CatBoostRegressor()
        m.load_model(str(mf))
        Xc = _clean(X)
        try:
            p = m.predict(Xc)
            return p.astype(np.float32)
        except Exception as pe:
            log.warning(
                "cat %s predict: %s",
                sym, pe,
            )
            return None
    except Exception as exc:
        log.warning("cat %s load: %s", sym, exc)
        return None


def predict_ridge(sym, X):
    mf = MODELS_DIR / ("ridge_" + sym + ".joblib")
    sf = MODELS_DIR / (
        "scaler_ridge_" + sym + ".joblib"
    )
    if not mf.exists() or not sf.exists():
        return None
    try:
        m = joblib.load(str(mf))
        s = joblib.load(str(sf))
        Xs = _clean(X)
        Xs = s.transform(Xs)
        return m.predict(Xs).astype(np.float32)
    except Exception as exc:
        log.warning("ridge %s: %s", sym, exc)
        return None


def predict_mlp(sym, X):
    mf = MODELS_DIR / ("mlp_" + sym + ".joblib")
    sf = MODELS_DIR / (
        "scaler_mlp_" + sym + ".joblib"
    )
    if not mf.exists() or not sf.exists():
        return None
    try:
        m = joblib.load(str(mf))
        s = joblib.load(str(sf))
        Xs = _clean(X)
        Xs = s.transform(Xs)
        return m.predict(Xs).astype(np.float32)
    except Exception as exc:
        log.warning("mlp %s: %s", sym, exc)
        return None


def blend_symbol(sym, X, weights):
    preds = {}
    for name, fn in [
        ("lgb", predict_lgb),
        ("xgb", predict_xgb),
        ("cat", predict_cat),
        ("ridge", predict_ridge),
        ("mlp", predict_mlp),
    ]:
        p = fn(sym, X)
        if p is not None:
            preds[name] = p

    if not preds:
        log.warning("%s: no models loaded", sym)
        return None

    n = X.shape[0]
    if weights:
        w_use = {
            k: float(weights.get(k, 0.0))
            for k in preds
        }
    else:
        w_use = {k: 1.0 for k in preds}

    wsum = sum(w_use.values())
    if wsum <= 0:
        w_use = {k: 1.0 for k in preds}
        wsum = float(len(preds))

    blend = np.zeros(n, dtype=np.float64)
    wsum_arr = np.zeros(n, dtype=np.float64)
    skipped = 0
    for k, p in preds.items():
        sane = np.isfinite(p) & (
            np.abs(p) <= MAX_ABS_PCT
        )
        skipped += int((~sane).sum())
        w = w_use[k]
        blend += np.where(sane, p * w, 0.0)
        wsum_arr += np.where(sane, w, 0.0)

    mask = wsum_arr > 0
    blend[mask] = blend[mask] / wsum_arr[mask]
    blend[~mask] = 0.0
    blend = np.clip(
        blend, -MAX_ABS_PCT, MAX_ABS_PCT,
    )

    return {
        "blend": blend,
        "skipped": skipped,
        "models": list(preds.keys()),
    }


def run_symbol_backtest(
    sym, X_test, r_test, ts_test, weights,
):
    log.info("-" * 60)
    log.info("BACKTEST %s rows=%d", sym, len(X_test))
    log.info("-" * 60)

    if len(X_test) == 0:
        return []

    _, allowed = _sym_weights(weights, sym)

    res = blend_symbol(sym, X_test, weights)
    if res is None:
        log.warning("%s: blend failed", sym)
        return []

    log.info(
        "%s: models=%s skipped=%d allowed=%s",
        sym, "+".join(res["models"]),
        res["skipped"], allowed,
    )

    if not allowed:
        log.info("%s: trade_allowed=False", sym)
        return []

    preds = res["blend"]
    trades = []
    fee_pct = FEE_RATE * 100.0
    fund_pct = FUNDING_RATE * 100.0

    for i in range(len(preds)):
        p = float(preds[i])
        if abs(p) < THRESHOLD_PCT:
            continue
        ret_pct = float(r_test[i])
        if not np.isfinite(ret_pct):
            continue
        if p > 0:
            direction = "LONG"
            gross_pct = ret_pct
        else:
            direction = "SHORT"
            gross_pct = -ret_pct
        net_pct = gross_pct - fee_pct - fund_pct
        trades.append({
            "symbol": sym,
            "timestamp": ts_test[i].isoformat(),
            "direction": direction,
            "predicted_pct": round(p, 4),
            "actual_pct": round(ret_pct, 4),
            "net_pct": round(net_pct, 4),
            "notional": POSITION_NOTIONAL,
            "pnl_usd": round(
                POSITION_NOTIONAL * net_pct / 100.0,
                4,
            ),
        })

    log.info("%s: trades=%d", sym, len(trades))
    return trades


def compute_metrics(trades, years):
    if not trades:
        return {
            "n_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "total_pnl_usd": 0.0,
            "total_return_pct": 0.0,
            "avg_trade_pct": 0.0,
            "std_trade_pct": 0.0,
            "sharpe": 0.0,
            "sortino": 0.0,
            "max_drawdown_pct": 0.0,
            "years": round(years, 3),
        }
    n = len(trades)
    pnls = np.array(
        [t["pnl_usd"] for t in trades],
        dtype=np.float64,
    )
    pcts = np.array(
        [t["net_pct"] for t in trades],
        dtype=np.float64,
    )
    wins = int((pnls > 0).sum())
    losses = n - wins

    total_pnl = float(pnls.sum())
    total_ret_pct = (
        total_pnl / INITIAL_BALANCE * 100.0
    )
    mean_pct = float(pcts.mean())
    std_pct = (
        float(pcts.std(ddof=1)) if n > 1 else 0.0
    )

    sharpe = 0.0
    if std_pct > 0 and years > 0:
        tpy = n / years
        sharpe = (
            mean_pct / std_pct * np.sqrt(tpy)
        )

    downside = pcts[pcts < 0]
    sortino = 0.0
    if len(downside) > 1 and years > 0:
        dstd = float(downside.std(ddof=1))
        if dstd > 0:
            tpy = n / years
            sortino = (
                mean_pct / dstd * np.sqrt(tpy)
            )

    cum = np.cumsum(pnls) + INITIAL_BALANCE
    peak = np.maximum.accumulate(cum)
    dd = (cum - peak) / peak * 100.0
    max_dd = float(dd.min()) if len(dd) else 0.0

    return {
        "n_trades": n,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / n, 4),
        "total_pnl_usd": round(total_pnl, 4),
        "total_return_pct": round(total_ret_pct, 4),
        "avg_trade_pct": round(mean_pct, 4),
        "std_trade_pct": round(std_pct, 4),
        "sharpe": round(float(sharpe), 3),
        "sortino": round(float(sortino), 3),
        "max_drawdown_pct": round(max_dd, 3),
        "years": round(years, 3),
    }


def main():
    log.info("=" * 60)
    log.info("ARGUS-Trader BACKTEST v3")
    log.info("SYMBOLS=%s", SYMBOLS_LIST)
    log.info(
        "THRESHOLD=%.2f%% NOTIONAL=$%.2f",
        THRESHOLD_PCT, POSITION_NOTIONAL,
    )
    log.info(
        "FEE=%.4f FUNDING=%.4f TEST_FRAC=%.2f",
        FEE_RATE, FUNDING_RATE, TEST_FRAC,
    )
    log.info("=" * 60)

    ds.SYMBOLS = SYMBOLS_LIST
    ds.REFERENCE = SYMBOLS_LIST[0]
    ds.DB2_SYMBOLS = DB2_SET

    data = ds.prepare(test_frac=TEST_FRAC)
    if data is None:
        log.error("prepare failed")
        return

    X_test = data["X_test"]
    r_test = data["r_test"]
    ts_test = data.get("ts_test")
    sym_test = data.get("sym_test")

    if ts_test is None or sym_test is None:
        log.error(
            "dataset.py missing ts_test/sym_test."
        )
        return

    log.info(
        "test rows: %d features: %d",
        len(X_test), len(data["feature_cols"]),
    )

    ts_sorted = sorted(ts_test)
    span_sec = (
        ts_sorted[-1] - ts_sorted[0]
    ).total_seconds()
    years = span_sec / (365.25 * 24 * 3600)
    log.info("test span: %.2f years", years)

    weights = load_weights()
    log.info(
        "weights: %d symbols",
        len(weights.get("symbols", {})),
    )

    sym_to_idx = {}
    for i, s in enumerate(sym_test):
        sym_to_idx.setdefault(s, []).append(i)

    all_trades = []
    per_symbol = {}

    for sym in sorted(sym_to_idx.keys()):
        idxs = sym_to_idx[sym]
        Xs = X_test[idxs]
        rs = r_test[idxs]
        ts = [ts_test[i] for i in idxs]
        trades = run_symbol_backtest(
            sym, Xs, rs, ts, weights,
        )
        all_trades.extend(trades)
        m = compute_metrics(trades, years)
        per_symbol[sym] = m
        log.info(
            "%s: n=%d wr=%.2f%% pnl=$%.2f sharpe=%.2f",
            sym, m["n_trades"],
            m.get("win_rate", 0) * 100,
            m.get("total_pnl_usd", 0),
            m.get("sharpe", 0),
        )

    all_trades.sort(key=lambda t: t["timestamp"])
    total_m = compute_metrics(all_trades, years)

    log.info("=" * 60)
    log.info("PORTFOLIO RESULT")
    log.info(
        "trades=%d wins=%d losses=%d wr=%.2f%%",
        total_m["n_trades"],
        total_m.get("wins", 0),
        total_m.get("losses", 0),
        total_m.get("win_rate", 0) * 100,
    )
    log.info(
        "pnl=$%+.2f (%.2f%% on $%.2f)",
        total_m["total_pnl_usd"],
        total_m["total_return_pct"],
        INITIAL_BALANCE,
    )
    log.info(
        "avg=%.4f%% std=%.4f%%",
        total_m.get("avg_trade_pct", 0),
        total_m.get("std_trade_pct", 0),
    )
    log.info(
        "sharpe=%.3f sortino=%.3f max_dd=%.2f%%",
        total_m.get("sharpe", 0),
        total_m.get("sortino", 0),
        total_m.get("max_drawdown_pct", 0),
    )
    log.info("=" * 60)

    out = {
        "run_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "config": {
            "threshold_pct": THRESHOLD_PCT,
            "max_abs_pct": MAX_ABS_PCT,
            "initial_balance": INITIAL_BALANCE,
            "notional": POSITION_NOTIONAL,
            "fee_rate": FEE_RATE,
            "funding_rate": FUNDING_RATE,
            "test_frac": TEST_FRAC,
            "symbols": SYMBOLS_LIST,
        },
        "test_years": round(years, 3),
        "total": total_m,
        "per_symbol": per_symbol,
        "trades": all_trades,
    }
    out_path = SCRIPT_DIR / "backtest_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            out, f,
            ensure_ascii=False, indent=2,
        )
    log.info("saved: %s", out_path.name)


if __name__ == "__main__":
    main()