# ============================================================
# ARGUS-Trader - PREDICT v7 [PRODUCTION]
# ------------------------------------------------------------
# v7: regression-aware. Reads meta.objective.
#     regression: model.predict() = return %.
#       Maps to prob_up in [0,1] and conf in [0,1].
#     binary: legacy path.
#     Sanity: prob_up, confidence always in [0,1].
#     Adds objective + model_version to output.
#     Adds predicted_return_pct for regression.
# v6: model_accuracy in top-level JSON.
# v5: per-symbol models. USE_CROSS=0.
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

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

import dataset as ds
from dataset import (
    INTERNAL_COLS,
    _get_feat_map,
    fetch_features,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.learn.predict")

MODELS_DIR = SCRIPT_DIR / "models"
LOOKBACK = 500

# Map predicted_return (in %) to prob_up in [0,1].
# +SCALE_PCT saturates to PROB_MAX, -SCALE_PCT to PROB_MIN.
SCALE_PCT = 4.0
PROB_MIN = 0.05
PROB_MAX = 0.95

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


def load_model_meta(sym):
    mf = MODELS_DIR / ("lgb_" + sym + ".txt")
    meta_f = MODELS_DIR / ("meta_" + sym + ".json")
    if not mf.exists() or not meta_f.exists():
        return None, None
    try:
        with open(
            meta_f, "r", encoding="utf-8"
        ) as f:
            meta = json.load(f)
        model = lgb.Booster(model_file=str(mf))
        return model, meta
    except Exception as e:
        log.warning("%s: %s", sym, e)
        return None, None


def _map_return_to_prob(pred_pct):
    """Map predicted return % to prob_up in [0,1]."""
    p = 0.5 + float(pred_pct) / SCALE_PCT
    if p < PROB_MIN:
        return PROB_MIN
    if p > PROB_MAX:
        return PROB_MAX
    return p


def _norm_confidence(pred_pct):
    """|pred| / SCALE_PCT, clipped to [0,1]."""
    c = abs(float(pred_pct)) / SCALE_PCT
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


def _build_row(f):
    row = []
    for col in INTERNAL_COLS:
        v = f.get(col, np.nan)
        if v is None:
            v = np.nan
        row.append(v)
    return row


def _get_model_acc(meta):
    for key in (
        "accuracy",
        "sign_acc_test",
        "ic_test",
    ):
        v = meta.get(key)
        if v is not None:
            return v
    return None


def predict_one(symbol):
    model, meta = load_model_meta(symbol)
    if model is None:
        log.warning("%s: no model", symbol)
        return None

    objective = str(
        meta.get("objective") or "binary"
    )
    version = str(meta.get("version") or "?")

    ds.SYMBOLS = [symbol]
    ds.REFERENCE = symbol
    ds.DB2_SYMBOLS = (
        {symbol} if symbol in DB2_SET else set()
    )

    base_cols = ["symbol", "timestamp"]
    base_cols += INTERNAL_COLS
    rows = fetch_features(symbol, limit=LOOKBACK)
    if not rows:
        log.warning("%s: no features", symbol)
        return None

    fmap = _get_feat_map(rows, base_cols)
    ts = sorted(fmap.keys())[-1]
    f = fmap[ts]
    row = _build_row(f)

    X = np.array([row], dtype=np.float32)
    raw = float(model.predict(X)[0])

    pred_pct = None

    if objective == "regression":
        pred_pct = raw
        prob_up = _map_return_to_prob(pred_pct)
        conf = _norm_confidence(pred_pct)
        direction = 1 if pred_pct > 0 else 0
    else:
        # legacy binary
        prob_up = _clip01(raw)
        direction = 1 if prob_up > 0.5 else 0
        conf = abs(prob_up - 0.5) * 2.0

    # hard sanity, both paths
    prob_up = _clip01(prob_up)
    conf = _clip01(conf)

    out = {
        "symbol": symbol,
        "timestamp": ts.isoformat(),
        "prob_up": round(prob_up, 4),
        "direction": direction,
        "confidence": round(conf, 4),
        "model_acc": _get_model_acc(meta),
        "objective": objective,
        "model_version": version,
    }
    if pred_pct is not None:
        out["predicted_return_pct"] = round(
            float(pred_pct), 4
        )
    return out


def main():
    log.info("=" * 60)
    log.info(
        "ARGUS-Trader PREDICT v7 "
        "(regression-aware)"
    )
    log.info("SYMBOLS=%s", SYMBOLS_LIST)
    log.info("=" * 60)

    results = []
    accs = []
    for sym in SYMBOLS_LIST:
        r = predict_one(sym)
        if r:
            results.append(r)
            a = r.get("model_acc")
            if a is not None:
                accs.append(a)

            extra = ""
            if "predicted_return_pct" in r:
                extra = " ret=%.4f%%" % (
                    r["predicted_return_pct"],
                )
            log.info(
                "%s: prob_up=%.4f "
                "dir=%d conf=%.4f%s",
                r["symbol"], r["prob_up"],
                r["direction"], r["confidence"],
                extra,
            )

    avg_acc = None
    if accs:
        avg_acc = round(
            sum(accs) / len(accs), 4
        )

    out = {
        "predicted_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "model_accuracy": avg_acc,
        "model_accuracy_avg": avg_acc,
        "symbols": SYMBOLS_LIST,
        "predictions": results,
    }

    out_path = SCRIPT_DIR / "last_predictions.json"
    with open(
        out_path, "w", encoding="utf-8"
    ) as f:
        json.dump(
            out, f,
            ensure_ascii=False, indent=2,
        )
    log.info("saved: %s", out_path.name)


if __name__ == "__main__":
    main()