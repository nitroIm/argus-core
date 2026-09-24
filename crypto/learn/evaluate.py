# ============================================================
# ARGUS-Trader - EVALUATE
# ------------------------------------------------------------
# Метрики модели на тесте:
#   accuracy, precision, recall, F1, confusion matrix.
# ============================================================

import sys
import logging
from pathlib import Path

import numpy as np
import lightgbm as lgb

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from dataset import prepare

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.learn.evaluate")

MODEL_FILE = SCRIPT_DIR / "models" / "lgb_model.txt"


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


def evaluate():
    log.info("=" * 60)
    log.info("ARGUS-Trader EVALUATE")
    log.info("=" * 60)

    if not MODEL_FILE.exists():
        log.error("model not found: %s", MODEL_FILE)
        return None

    data = prepare()
    if data is None:
        log.error("no data")
        return None

    model = lgb.Booster(model_file=str(MODEL_FILE))

    X_test = data["X_test"]
    y_test = data["y_test"]

    y_prob = model.predict(X_test)
    y_pred = (y_prob > 0.5).astype(int)

    m = metrics(y_test, y_pred)

    log.info("test samples: %d", len(y_test))
    log.info(
        "accuracy=%.4f precision=%.4f "
        "recall=%.4f f1=%.4f",
        m["accuracy"], m["precision"],
        m["recall"], m["f1"],
    )
    log.info(
        "confusion: TP=%d TN=%d FP=%d FN=%d",
        m["tp"], m["tn"], m["fp"], m["fn"],
    )

    # Baseline: always predict majority
    up = int(y_test.sum())
    down = len(y_test) - up
    baseline = max(up, down) / len(y_test)
    log.info(
        "baseline (always majority): %.4f",
        baseline,
    )
    log.info(
        "edge over baseline: %+.4f",
        m["accuracy"] - baseline,
    )

    return m


def main():
    m = evaluate()
    if m is None:
        log.error("evaluate failed")
        return
    log.info("=" * 60)


if __name__ == "__main__":
    main()