# ============================================================
# ARGUS-Trader - PREDICT v3 [PRODUCTION]
# ------------------------------------------------------------
# v3: читает FEATURE_COLS из model_meta.json
#     (всегда согласован с обученной моделью)
# v2: reads external через fetch
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
sys.path.insert(0, str(SCRIPT_DIR))

from db import get_connection

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


def fetch_last_row(symbol, feature_cols):
    """Читает последнюю строку features_hourly."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cols = ", ".join(feature_cols)
                sql = (
                    "SELECT " + cols
                    + " FROM features_hourly "
                    + "WHERE symbol = %s "
                    + "ORDER BY timestamp DESC "
                    + "LIMIT 1"
                )
                cur.execute(sql, (symbol,))
                row = cur.fetchone()
                if not row:
                    return None
                out = []
                for v in row:
                    if v is None:
                        out.append(np.nan)
                    else:
                        try:
                            out.append(float(v))
                        except Exception:
                            out.append(np.nan)
                return np.array([out], dtype=np.float32)
    except Exception as e:
        log.error(
            "fetch_last_row %s: %s", symbol, e,
        )
        return None


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

    feature_cols = meta.get("features", [])
    if not feature_cols:
        log.error("meta has no features list")
        return None

    log.info(
        "model features: %d (accuracy=%.4f)",
        len(feature_cols),
        meta.get("accuracy", 0),
    )

    model = lgb.Booster(model_file=str(MODEL_FILE))

    results = []
    for symbol in symbols:
        X = fetch_last_row(symbol, feature_cols)
        if X is None:
            log.warning("%s: no data", symbol)
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
        "model_features": len(feature_cols),
        "predictions": results,
    }


def main():
    log.info("=" * 60)
    log.info("ARGUS-Trader PREDICT v3")
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