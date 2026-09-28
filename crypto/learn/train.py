# ============================================================
# ARGUS-Trader - TRAIN [PRODUCTION]
# ------------------------------------------------------------
# v5: читает best_params.json если есть (от autotune)
# v4: регуляризация для малых данных
# v3: is_unbalance + prev
# ============================================================

import sys
import json
import shutil
import logging
from pathlib import Path
from datetime import datetime, timezone

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
log = logging.getLogger("crypto.learn.train")

MODELS_DIR = SCRIPT_DIR / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

MODEL_FILE = MODELS_DIR / "lgb_model.txt"
META_FILE = MODELS_DIR / "model_meta.json"

PREV_DIR = MODELS_DIR / "prev"
PREV_MODEL = PREV_DIR / "lgb_model.txt"
PREV_META = PREV_DIR / "model_meta.json"

BEST_PARAMS_FILE = MODELS_DIR / "best_params.json"

MIN_SAMPLES = 200

PARAMS = {
    "objective": "binary",
    "is_unbalance": True,
    "metric": "binary_logloss",
    "boosting_type": "gbdt",
    "num_leaves": 15,
    "max_depth": 4,
    "learning_rate": 0.05,
    "feature_fraction": 0.6,
    "bagging_fraction": 0.7,
    "bagging_freq": 5,
    "min_data_in_leaf": 40,
    "lambda_l1": 0.5,
    "lambda_l2": 0.5,
    "verbose": -1,
    "seed": 42,
}

NUM_ROUNDS = 300
EARLY_STOP = 40


def load_best_params():
    """Читает best_params.json если есть."""
    if not BEST_PARAMS_FILE.exists():
        log.info("no best_params, using defaults")
        return False
    try:
        with open(BEST_PARAMS_FILE, "r",
                  encoding="utf-8") as f:
            bp = json.load(f)
        best = bp.get("best", {})
        cfg = best.get("params", {})
        if not cfg:
            log.warning("empty best_params")
            return False
        PARAMS.update(cfg)
        log.info(
            "loaded best_params: %s "
            "(edge=%+.4f from autotune)",
            cfg, best.get("edge", 0),
        )
        return True
    except Exception as e:
        log.warning("best_params load: %s", e)
        return False


def save_prev():
    if not MODEL_FILE.exists():
        log.info("prev: no current model")
        return
    PREV_DIR.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(MODEL_FILE, PREV_MODEL)
        if META_FILE.exists():
            shutil.copy2(META_FILE, PREV_META)
        log.info("prev: current moved to prev/")
    except Exception as e:
        log.warning("prev save: %s", e)


def train(symbol=None):
    log.info("=" * 60)
    log.info("ARGUS-Trader TRAIN v5")
    log.info("=" * 60)

    load_best_params()

    data = prepare(symbol=symbol)
    if data is None:
        log.error("no data")
        return None

    if data["n_train"] < MIN_SAMPLES:
        log.warning(
            "not enough samples: %d < %d",
            data["n_train"], MIN_SAMPLES,
        )

    X_train = data["X_train"]
    y_train = data["y_train"]
    X_test = data["X_test"]
    y_test = data["y_test"]

    log.info(
        "train=%d test=%d",
        len(X_train), len(X_test),
    )

    train_set = lgb.Dataset(X_train, label=y_train)
    valid_set = lgb.Dataset(
        X_test, label=y_test, reference=train_set,
    )

    log.info("training...")
    model = lgb.train(
        PARAMS, train_set,
        num_boost_round=NUM_ROUNDS,
        valid_sets=[valid_set],
        callbacks=[
            lgb.early_stopping(EARLY_STOP),
            lgb.log_evaluation(50),
        ],
    )

    y_pred_prob = model.predict(X_test)
    y_pred = (y_pred_prob > 0.5).astype(int)
    acc = float((y_pred == y_test).mean())

    log.info("test accuracy: %.4f", acc)

    tp = int(((y_test == 1) & (y_pred == 1)).sum())
    tn = int(((y_test == 0) & (y_pred == 0)).sum())
    fp = int(((y_test == 0) & (y_pred == 1)).sum())
    fn = int(((y_test == 1) & (y_pred == 0)).sum())
    log.info(
        "confusion: TP=%d TN=%d FP=%d FN=%d",
        tp, tn, fp, fn,
    )

    importance = model.feature_importance(
        importance_type="gain"
    )
    feat_names = data["feature_cols"]
    pairs = sorted(
        zip(feat_names, importance),
        key=lambda x: -x[1],
    )
    log.info("top features:")
    for name, score in pairs[:10]:
        log.info("  %s: %.2f", name, score)

    save_prev()
    model.save_model(str(MODEL_FILE))
    log.info("saved model: %s", MODEL_FILE.name)

    meta = {
        "trained_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "version": "v5",
        "n_total": data["n_total"],
        "n_train": data["n_train"],
        "n_test": data["n_test"],
        "accuracy": round(acc, 4),
        "num_trees": model.num_trees(),
        "features": feat_names,
        "top_features": [
            {"name": n, "gain": round(float(s), 2)}
            for n, s in pairs[:10]
        ],
        "balance": data["balance"],
        "symbol": symbol,
        "confusion": {
            "tp": tp, "tn": tn,
            "fp": fp, "fn": fn,
        },
    }
    with open(META_FILE, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    log.info("saved meta: %s", META_FILE.name)

    return meta


def main():
    meta = train()
    if meta is None:
        log.error("train failed")
        return
    log.info("=" * 60)
    log.info(
        "DONE. accuracy=%.4f trees=%d",
        meta["accuracy"], meta["num_trees"],
    )
    log.info("=" * 60)


if __name__ == "__main__":
    main()