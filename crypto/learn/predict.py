# ============================================================
# ARGUS-Trader - PREDICT v11
# ------------------------------------------------------------
# v11: ensemble — average of LightGBM + XGBoost.
#      Falls back to LGB only if XGB missing.
#      Checks feature count for both.
# v10: feature count sanity.
# v9:  use dataset.prepare_one.
# ============================================================

import os
os.environ.setdefault("USE_CROSS", "0")

import sys
import json
import logging
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
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
log = logging.getLogger("crypto.learn.predict")

MODELS_DIR = SCRIPT_DIR / "models"

SCALE_PCT = 4.0
PROB_MIN = 0.05
PROB_MAX = 0.95

LGB_WEIGHT = 0.5
XGB_WEIGHT = 0.5

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


def load_lgb(sym):
    mf = MODELS_DIR / ("lgb_" + sym + ".txt")
    meta_f = MODELS_DIR / ("meta_" + sym + ".json")
    if not mf.exists() or not meta_f.exists():
        return None, None
    try:
        with open(meta_f, "r", encoding="utf-8") as f:
            meta = json.load(f)
        model = lgb.Booster(model_file=str(mf))
        return model, meta
    except Exception as exc:
        log.warning("lgb %s: %s", sym, exc)
        return None, None


def load_xgb(sym):
    mf = MODELS_DIR / ("xgb_" + sym + ".json")
    meta_f = MODELS_DIR / ("meta_xgb_" + sym + ".json")
    if not mf.exists() or not meta_f.exists():
        return None, None
    try:
        with open(meta_f, "r", encoding="utf-8") as f:
            meta = json.load(f)
        model = xgb.Booster()
        model.load_model(str(mf))
        return model, meta
    except Exception as exc:
        log.warning("xgb %s: %s", sym, exc)
        return None, None


def _map_ret_to_prob(p):
    x = 0.5 + float(p) / SCALE_PCT
    if x < PROB_MIN:
        return PROB_MIN
    if x > PROB_MAX:
        return PROB_MAX
    return x


def _conf(p):
    c = abs(float(p)) / SCALE_PCT
    if c < 0.0:
        return 0.0
    if c > 1.0:
        return 1.0
    return c


def _clip01(v):
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


def _get_lgb_acc(meta):
    for key in ("accuracy", "sign_acc_test", "ic_test"):
        v = meta.get(key)
        if v is not None:
            return v
    return None


def predict_one(symbol):
    lgb_m, lgb_meta = load_lgb(symbol)
    xgb_m, xgb_meta = load_xgb(symbol)

    if lgb_m is None and xgb_m is None:
        log.warning("%s: no models", symbol)
        return None

    expected = len(ds.FEATURE_COLS)

    if lgb_m is not None and lgb_m.num_feature() != expected:
        log.warning(
            "%s: lgb has %d feat, need %d -> skip lgb",
            symbol, lgb_m.num_feature(), expected,
        )
        lgb_m = None
        lgb_meta = None

    if xgb_m is not None:
        try:
            xgb_nf = xgb_m.num_features()
        except Exception:
            xgb_nf = None
        if xgb_nf is not None and xgb_nf != expected:
            log.warning(
                "%s: xgb has %d feat, need %d -> skip xgb",
                symbol, xgb_nf, expected,
            )
            xgb_m = None
            xgb_meta = None

    if lgb_m is None and xgb_m is None:
        return None

    ds.SYMBOLS = [symbol]
    ds.REFERENCE = symbol
    ds.DB2_SYMBOLS = (
        {symbol} if symbol in DB2_SET else set()
    )

    ts, row = ds.prepare_one(symbol)
    if row is None:
        log.warning("%s: no features", symbol)
        return None

    if len(row) != expected:
        log.warning(
            "%s: row has %d, need %d -> skip",
            symbol, len(row), expected,
        )
        return None

    X = np.array([row], dtype=np.float32)

    preds = []
    weights = []
    sources = []

    if lgb_m is not None:
        p_lgb = float(lgb_m.predict(X)[0])
        preds.append(p_lgb)
        weights.append(LGB_WEIGHT)
        sources.append("lgb")

    if xgb_m is not None:
        d = xgb.DMatrix(X)
        try:
            best_iter = None
            try:
                best_iter = xgb_m.best_iteration
            except Exception:
                pass
            if best_iter is not None:
                p_xgb = float(xgb_m.predict(
                    d, iteration_range=(0, best_iter + 1)
                )[0])
            else:
                p_xgb = float(xgb_m.predict(d)[0])
        except Exception as exc:
            log.warning("xgb predict %s: %s", symbol, exc)
            p_xgb = None
        if p_xgb is not None:
            preds.append(p_xgb)
            weights.append(XGB_WEIGHT)
            sources.append("xgb")

    if not preds:
        return None

    wsum = sum(weights)
    pred_pct = sum(
        p * w for p, w in zip(preds, weights)
    ) / wsum

    prob_up = _map_ret_to_prob(pred_pct)
    conf = _conf(pred_pct)
    direction = 1 if pred_pct > 0 else 0

    prob_up = _clip01(prob_up)
    conf = _clip01(conf)

    meta_for_acc = lgb_meta or xgb_meta
    acc = _get_lgb_acc(meta_for_acc) if meta_for_acc else None

    out = {
        "symbol": symbol,
        "timestamp": ts.isoformat(),
        "prob_up": round(prob_up, 4),
        "direction": direction,
        "confidence": round(conf, 4),
        "predicted_return_pct": round(float(pred_pct), 4),
        "model_acc": acc,
        "objective": "regression",
        "model_version": "v11-blend",
        "sources": sources,
        "blend": len(sources) > 1,
        "features_used": expected,
    }
    if len(sources) > 1:
        out["lgb_pred"] = round(float(preds[0]), 4)
        out["xgb_pred"] = round(float(preds[1]), 4)
    return out


def main():
    log.info("=" * 60)
    log.info("ARGUS-Trader PREDICT v11 (blend lgb+xgb)")
    log.info("SYMBOLS=%s", SYMBOLS_LIST)
    log.info("FEATURE_COLS=%d", len(ds.FEATURE_COLS))
    log.info("WEIGHTS: lgb=%.2f xgb=%.2f",
             LGB_WEIGHT, XGB_WEIGHT)
    log.info("=" * 60)

    results = []
    accs = []

    for sym in SYMBOLS_LIST:
        r = predict_one(sym)
        if not r:
            continue
        results.append(r)
        a = r.get("model_acc")
        if a is not None:
            accs.append(a)

        extra = ""
        if "lgb_pred" in r and "xgb_pred" in r:
            extra = " lgb=%.4f xgb=%.4f" % (
                r["lgb_pred"], r["xgb_pred"]
            )
        log.info(
            "%s: prob_up=%.4f dir=%d "
            "conf=%.4f ret=%.4f%% [%s]%s",
            r["symbol"], r["prob_up"],
            r["direction"], r["confidence"],
            r["predicted_return_pct"],
            "+".join(r["sources"]),
            extra,
        )

    avg_acc = None
    if accs:
        avg_acc = round(sum(accs) / len(accs), 4)

    out = {
        "predicted_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "model_accuracy": avg_acc,
        "model_accuracy_avg": avg_acc,
        "feature_count": len(ds.FEATURE_COLS),
        "mode": "blend",
        "symbols": SYMBOLS_LIST,
        "predictions": results,
    }

    out_path = SCRIPT_DIR / "last_predictions.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log.info("saved: %s", out_path.name)


if __name__ == "__main__":
    main()