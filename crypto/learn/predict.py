# ============================================================
# ARGUS-Trader - PREDICT v2 [PRODUCTION]
# ------------------------------------------------------------
# v2: читает features_hourly + external_market
# v1: базовое предсказание
# ============================================================

import sys
import json
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

import numpy as np
import lightgbm as lgb

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))

from db import get_connection
from dataset import (
    FEATURE_COLS, EXTERNAL_COLS, EXT_MAX_AGE_H,
    fetch_external, fetch_eth_btc, ext_lookup,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.learn.predict")

MODEL_FILE = SCRIPT_DIR / "models" / "lgb_model.txt"
META_FILE = SCRIPT_DIR / "models" / "model_meta.json"


def load_meta():
    if not META_FILE.exists():
        return None
    try:
        with open(META_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def fetch_last_internal(symbol):
    """Читает последнюю строку features_hourly (без external)."""
    internal = [
        c for c in FEATURE_COLS
        if c not in EXTERNAL_COLS
    ]
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                sql = (
                    "SELECT timestamp, "
                    + ", ".join(internal)
                    + " FROM features_hourly "
                    + "WHERE symbol = %s "
                    + "ORDER BY timestamp DESC "
                    + "LIMIT 1"
                )
                cur.execute(sql, (symbol,))
                return cur.fetchone()
    except Exception as e:
        log.error("fetch_last %s: %s", symbol, e)
        return None


def build_row(symbol, row, ext_dxy, ext_spx,
              ext_gold, eth_btc):
    if row is None:
        return None

    ts = row[0]
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    n = len(FEATURE_COLS) - len(EXTERNAL_COLS)
    feats = []
    for v in row[1:1 + n]:
        if v is None:
            feats.append(np.nan)
        else:
            try:
                feats.append(float(v))
            except Exception:
                feats.append(np.nan)

    dxy = ext_lookup(ext_dxy, ts)
    spx = ext_lookup(ext_spx, ts)
    gold = ext_lookup(ext_gold, ts)

    feats.append(dxy if dxy is not None else np.nan)
    feats.append(spx if spx is not None else np.nan)
    feats.append(gold if gold is not None else np.nan)

    if symbol == "ETHUSDT":
        ratio = eth_btc.get(ts)
        feats.append(
            ratio if ratio is not None else np.nan
        )
    else:
        feats.append(np.nan)

    return np.array([feats], dtype=np.float32)


def predict_all(symbols=None):
    if symbols is None:
        symbols = ["BTCUSDT", "ETHUSDT"]

    if not MODEL_FILE.exists():
        log.error("model not found")
        return None

    meta = load_meta()
    if not meta:
        log.error("meta missing")
        return None

    model = lgb.Booster(model_file=str(MODEL_FILE))

    # Один раз подгружаем external
    ext_dxy = fetch_external("DXY")
    ext_spx = fetch_external("SPX")
    ext_gold = fetch_external("GOLD")
    eth_btc = fetch_eth_btc()

    results = []
    for symbol in symbols:
        row = fetch_last_internal(symbol)
        if row is None:
            log.warning("%s: no data", symbol)
            continue

        X = build_row(
            symbol, row,
            ext_dxy, ext_spx, ext_gold, eth_btc,
        )
        if X is None:
            continue

        prob_up = float(model.predict(X)[0])
        direction = 1 if prob_up > 0.5 else 0

        r = {
            "symbol": symbol,
            "prob_up": round(prob_up, 4),
            "direction": direction,
            "confidence": round(
                abs(prob_up - 0.5) * 2, 4
            ),
        }
        results.append(r)
        log.info(
            "%s: prob_up=%.4f dir=%d conf=%.4f",
            r["symbol"], r["prob_up"],
            r["direction"], r["confidence"],
        )

    return {
        "predicted_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "model_accuracy": meta.get("accuracy"),
        "predictions": results,
    }


def main():
    log.info("=" * 60)
    log.info("ARGUS-Trader PREDICT v2")
    log.info("=" * 60)

    result = predict_all()
    if result is None:
        log.error("predict failed")
        return

    out = SCRIPT_DIR / "last_predictions.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    log.info("saved: %s", out.name)


if __name__ == "__main__":
    main()