# ============================================================
# ARGUS-Trader - BACKTEST v7
# ------------------------------------------------------------
# v7: Meta-labeling filter (trade only when meta_prob > thr).
#      Per-regime model selection.
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

THRESHOLD_PCT = float(os.getenv("BT_THRESHOLD_PCT", "2.0"))
META_THRESHOLD = float(os.getenv("BT_META_THRESHOLD", "0.55"))
MAX_ABS_PCT = float(os.getenv("BT_MAX_ABS_PCT", "15.0"))
INITIAL_BALANCE = float(os.getenv("BT_BALANCE", "1000.0"))
POSITION_NOTIONAL = float(os.getenv("BT_NOTIONAL", "20.0"))
FEE_RATE = float(os.getenv("BT_FEE_RATE", "0.0014"))
FUNDING_RATE = float(os.getenv("BT_FUNDING", "0.0003"))
TEST_FRAC = float(os.getenv("BT_TEST_FRAC", "0.2"))

SYMBOLS_LIST = [
    s.strip().upper()
    for s in (os.getenv("SYMBOLS")
              or "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT").split(",")
    if s.strip()
]
DB2_SET = {
    s.strip().upper()
    for s in (os.getenv("DB2_SYMBOLS")
              or "SOLUSDT,BNBUSDT").split(",")
    if s.strip()
}


def _sym_weights(wdata, sym):
    if not wdata:
        return {}, True
    entry = wdata.get("symbols", {}).get(sym)
    if not entry:
        return {}, True
    return entry.get("weights") or {}, entry.get("trade_allowed", True)


def load_weights():
    p = MODELS_DIR / "ensemble_weights.json"
    if not p.exists():
        return {}
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def load_regime_models(sym):
    """Load all regime models for a symbol."""
    models = {}
    for reg in range(3):
        mf = MODELS_DIR / f"lgb_{sym}_reg{reg}.txt"
        xf = MODELS_DIR / f"xgb_{sym}_reg{reg}.json"
        if not mf.exists() or not xf.exists():
            continue
        lgb_m = lgb.Booster(model_file=str(mf))
        xgb_m = xgb.Booster()
        xgb_m.load_model(str(xf))
        models[reg] = {"lgb": lgb_m, "xgb": xgb_m}
    return models


def load_meta_model(sym):
    mf = MODELS_DIR / f"meta_xgb_{sym}.json"
    if not mf.exists():
        return None
    m = xgb.Booster()
    m.load_model(str(mf))
    return m


def run_symbol_backtest(sym, X_test, r_test, ts_test, regimes_test):
    log.info("-" * 60)
    log.info("BACKTEST %s rows=%d", sym, len(X_test))
    log.info("-" * 60)

    if len(X_test) == 0:
        return []

    regime_models = load_regime_models(sym)
    meta_model = load_meta_model(sym)

    if not regime_models:
        log.warning("%s: no regime models", sym)
        return []

    # Predict per regime
    probs = np.full((len(X_test), 3), 1.0 / 3.0, dtype=np.float32)
    covered = np.zeros(len(X_test), dtype=bool)
    for reg, models in regime_models.items():
        mask = regimes_test == reg
        if mask.sum() == 0:
            continue
        p_lgb = models["lgb"].predict(X_test[mask])
        p_xgb = models["xgb"].predict(
            xgb.DMatrix(X_test[mask]),
        )
        probs[mask] = (p_lgb + p_xgb) / 2.0
        covered[mask] = True

    # Meta filter
    if meta_model is not None:
        meta_X = np.column_stack([
            probs.max(axis=1),
            probs[:, 0] - probs[:, 2],
            regimes_test,
        ])
        meta_prob = meta_model.predict(xgb.DMatrix(meta_X))
    else:
        meta_prob = np.ones(len(X_test))

    trades = []
    fee_pct = FEE_RATE * 100.0
    fund_pct = FUNDING_RATE * 100.0

    for i in range(len(X_test)):
        if not covered[i]:
            continue
        if meta_prob[i] < META_THRESHOLD:
            continue
        p = probs[i]
        max_p = float(p.max())
        if max_p < 0.55:
            continue
        pred_label = int(np.argmax(p))
        if pred_label == 1:
            continue
        direction = "LONG" if pred_label == 2 else "SHORT"
        ret_pct = float(r_test[i])
        if not np.isfinite(ret_pct):
            continue
        gross_pct = ret_pct if pred_label == 2 else -ret_pct
        net_pct = gross_pct - fee_pct - fund_pct
        trades.append({
            "symbol": sym,
            "timestamp": ts_test[i].isoformat(),
            "direction": direction,
            "predicted_prob": round(max_p, 4),
            "meta_prob": round(float(meta_prob[i]), 4),
            "actual_pct": round(ret_pct, 4),
            "net_pct": round(net_pct, 4),
            "pnl_usd": round(POSITION_NOTIONAL * net_pct / 100.0, 4),
        })

    log.info("%s: trades=%d covered=%.2f", sym, len(trades), covered.mean())
    return trades


def compute_metrics(trades, years):
    if not trades:
        return {"n_trades": 0, "win_rate": 0.0, "total_pnl_usd": 0.0,
                "sharpe": 0.0, "sortino": 0.0, "max_drawdown_pct": 0.0}
    n = len(trades)
    pnls = np.array([t["pnl_usd"] for t in trades], dtype=np.float64)
    pcts = np.array([t["net_pct"] for t in trades], dtype=np.float64)
    wins = int((pnls > 0).sum())
    total_pnl = float(pnls.sum())
    mean_pct = float(pcts.mean())
    std_pct = float(pcts.std(ddof=1)) if n > 1 else 0.0
    sharpe = 0.0
    if std_pct > 0 and years > 0:
        tpy = n / years
        sharpe = mean_pct / std_pct * np.sqrt(tpy)
    downside = pcts[pcts < 0]
    sortino = 0.0
    if len(downside) > 1 and years > 0:
        dstd = float(downside.std(ddof=1))
        if dstd > 0:
            tpy = n / years
            sortino = mean_pct / dstd * np.sqrt(tpy)
    cum = np.cumsum(pnls) + INITIAL_BALANCE
    peak = np.maximum.accumulate(cum)
    dd = (cum - peak) / peak * 100.0
    max_dd = float(dd.min()) if len(dd) else 0.0
    return {
        "n_trades": n, "wins": wins, "losses": n - wins,
        "win_rate": round(wins / n, 4),
        "total_pnl_usd": round(total_pnl, 4),
        "total_return_pct": round(total_pnl / INITIAL_BALANCE * 100.0, 4),
        "avg_trade_pct": round(mean_pct, 4),
        "std_trade_pct": round(std_pct, 4),
        "sharpe": round(float(sharpe), 3),
        "sortino": round(float(sortino), 3),
        "max_drawdown_pct": round(max_dd, 3),
        "years": round(years, 3),
    }


def main():
    log.info("=" * 60)
    log.info("ARGUS-Trader BACKTEST v7 (meta + regime)")
    log.info("SYMBOLS=%s", SYMBOLS_LIST)
    log.info("META_THRESHOLD=%.2f", META_THRESHOLD)
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

    ts_sorted = sorted(ts_test)
    span_sec = (ts_sorted[-1] - ts_sorted[0]).total_seconds()
    years = span_sec / (365.25 * 24 * 3600)
    log.info("test span: %.2f years", years)

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
        regs = Xs[:, -1].astype(np.int32)
        trades = run_symbol_backtest(sym, Xs, rs, ts, regs)
        all_trades.extend(trades)
        m = compute_metrics(trades, years)
        per_symbol[sym] = m
        log.info("%s: n=%d wr=%.2f%% pnl=$%.2f sharpe=%.2f",
                 sym, m["n_trades"], m.get("win_rate", 0) * 100,
                 m.get("total_pnl_usd", 0), m.get("sharpe", 0))

    all_trades.sort(key=lambda t: t["timestamp"])
    total_m = compute_metrics(all_trades, years)

    log.info("=" * 60)
    log.info("PORTFOLIO RESULT")
    log.info("trades=%d wins=%d losses=%d wr=%.2f%%",
             total_m["n_trades"], total_m.get("wins", 0),
             total_m.get("losses", 0), total_m.get("win_rate", 0) * 100)
    log.info("pnl=$%+.2f (%.2f%% on $%.2f)",
             total_m["total_pnl_usd"], total_m["total_return_pct"],
             INITIAL_BALANCE)
    log.info("sharpe=%.3f sortino=%.3f max_dd=%.2f%%",
             total_m.get("sharpe", 0), total_m.get("sortino", 0),
             total_m.get("max_drawdown_pct", 0))
    log.info("=" * 60)

    out = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "meta_threshold": META_THRESHOLD,
            "initial_balance": INITIAL_BALANCE,
            "notional": POSITION_NOTIONAL,
        },
        "test_years": round(years, 3),
        "total": total_m,
        "per_symbol": per_symbol,
        "trades": all_trades,
    }
    out_path = SCRIPT_DIR / "backtest_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log.info("saved: %s", out_path.name)


if __name__ == "__main__":
    main()