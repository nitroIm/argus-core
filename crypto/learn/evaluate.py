# ============================================================
# ARGUS-Trader - EVALUATE v4
# ------------------------------------------------------------
# v4: per-symbol. Читает каждую lgb_{sym}.txt + meta_{sym}.json,
#     считает метрики по своей монете, выводит среднее.
# v3: одна модель.
# ============================================================

import os
os.environ.setdefault("USE_CROSS", "0")

import sys
import json
import logging
from pathlib import Path

import numpy as np
import lightgbm as lgb

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
log = logging.getLogger("crypto.learn.evaluate")

MODELS_DIR = SCRIPT_DIR / "models"

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


def metrics(y_true, y_pred):
    y_true = np.array(y_true)
    y_pred = np.array(y_pred)
    tp = int(((y_true == 1) & (y_pred == 1)).sum())
    tn = int(((y_true == 0) & (y_pred == 0)).sum())
    fp = int(((y_true == 0) & (y_pred == 1)).sum())
    fn = int(((y_true == 1) & (y_pred == 0)).sum())
    acc = (tp + tn) / max(len(y_true), 1)
    prec = tp / max(tp + fp, 1)
    rec = tp / max(tp + fn, 1)
    f1 = 2 * prec * rec / max(prec + rec, 1e-9)
    return {
        "accuracy": round(acc, 4),
        "precision": round(prec, 4),
        "recall": round(rec, 4),
        "f1": round(f1, 4),
        "tp": tp, "tn": tn, "fp": fp, "fn": fn,
    }


def eval_one(symbol):
    model_file = MODELS_DIR / ("lgb_" + symbol + ".txt")
    meta_file = MODELS_DIR / ("meta_" + symbol + ".json")
    if not model_file.exists() or not meta_file.exists():
        log.warning("%s: model or meta missing", symbol)
        return None

    ds.SYMBOLS = [symbol]
    ds.REFERENCE = symbol
    ds.DB2_SYMBOLS = (
        {symbol} if symbol in DB2_SET else set()
    )

    data = ds.prepare()
    if data is None:
        log.error("%s: no data", symbol)
        return None

    model = lgb.Booster(model_file=str(model_file))
    y_test = data["y_test"]
    X_test = data["X_test"]

    y_prob = model.predict(X_test)
    y_pred = (y_prob > 0.5).astype(int)
    m = metrics(y_test, y_pred)

    log.info(
        "%s: acc=%.4f prec=%.4f rec=%.4f f1=%.4f",
        symbol,
        m["accuracy"], m["precision"],
        m["recall"], m["f1"],
    )
    return m


def evaluate():
    log.info("=" * 60)
    log.info("ARGUS-Trader EVALUATE v4 (per-symbol)")
    log.info("SYMBOLS=%s", SYMBOLS_LIST)
    log.info("=" * 60)

    results = {}
    accs = []
    for sym in SYMBOLS_LIST:
        try:
            m = eval_one(sym)
            if m:
                results[sym] = m
                accs.append(m["accuracy"])
        except Exception as e:
            log.error("%s: %s", sym, e)

    if not results:
        log.error("no results")
        return None

    avg_acc = float(np.mean(accs)) if accs else 0.0
    avg_f1 = float(
        np.mean([m["f1"] for m in results.values()])
    )
    log.info(
        "AVG: acc=%.4f f1=%.4f (%d models)",
        avg_acc, avg_f1, len(results),
    )
    return {
        "accuracy": round(avg_acc, 4),
        "f1": round(avg_f1, 4),
        "per_symbol": results,
    }


def main():
    m = evaluate()
    if m is None:
        log.error("evaluate failed")
        return
    log.info("=" * 60)


if __name__ == "__main__":
    main()