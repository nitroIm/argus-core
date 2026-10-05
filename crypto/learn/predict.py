# ============================================================
# ARGUS-Trader - PREDICT v6 [PRODUCTION]
# ------------------------------------------------------------
# v6: model_accuracy в top-level JSON (для signals.py).
# v5: per-symbol модели. USE_CROSS=0 — как в train.
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
        with open(meta_f, "r", encoding="utf-8") as f:
            meta = json.load(f)
        model = lgb.Booster(model_file=str(mf))
        return model, meta
    except Exception as e:
        log.warning("%s: %s", sym, e)
        return None, None


def predict_one(symbol):
    model, meta = load_model_meta(symbol)
    if model is None:
        log.warning("%s: no model", symbol)
        return None

    ds.SYMBOLS = [symbol]
    ds.REFERENCE = symbol
    ds.DB2_SYMBOLS = (
        {symbol} if symbol in DB2_SET else set()
    )

    base_cols = ["symbol", "timestamp"] + INTERNAL_COLS
    rows = fetch_features(symbol, limit=LOOKBACK)
    if not rows:
        log.warning("%s: no features", symbol)
        return None

    fmap = _get_feat_map(rows, base_cols)
    ts = sorted(fmap.keys())[-1]
    f = fmap[ts]

    row = []
    for col in INTERNAL_COLS:
        v = f.get(col, np.nan)
        row.append(v if v is not None else np.nan)

    X = np.array([row], dtype=np.float32)
    prob_up = float(model.predict(X)[0])
    direction = 1 if prob_up > 0.5 else 0

    return {
        "symbol": symbol,
        "timestamp": ts.isoformat(),
        "prob_up": round(prob_up, 4),
        "direction": direction,
        "confidence": round(
            abs(prob_up - 0.5) * 2, 4
        ),
        "model_acc": meta.get("accuracy"),
    }


def main():
    log.info("=" * 60)
    log.info("ARGUS-Trader PREDICT v6 (per-symbol)")
    log.info("SYMBOLS=%s", SYMBOLS_LIST)
    log.info("=" * 60)

    results = []
    accs = []
    for sym in SYMBOLS_LIST:
        r = predict_one(sym)
        if r:
            results.append(r)
            if r.get("model_acc") is not None:
                accs.append(r["model_acc"])
            log.info(
                "%s: prob_up=%.4f dir=%d conf=%.4f",
                r["symbol"], r["prob_up"],
                r["direction"], r["confidence"],
            )

    avg_acc = (
        round(sum(accs) / len(accs), 4)
        if accs else None
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
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log.info("saved: %s", out_path.name)


if __name__ == "__main__":
    main()