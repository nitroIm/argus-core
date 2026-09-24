# ============================================================
# ARGUS-Trader - TRAIN
# ------------------------------------------------------------
# Обучение LightGBM на features_hourly.
# Модель сохраняется в learn/models/.
# ============================================================

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

MIN_SAMPLES = 200

PARAMS = {
    "objective": "binary",
    "metric": "binary_logloss",
    "boosting_type": "gbdt",
    "num_leaves": 31,
    "learning_rate": 0.05,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 5,
    "min_data_in_leaf": 20,
    "verbose": -1,
    "seed": 42,
}

NUM_ROUNDS = 200
EARLY_STOP = 30


def train(symbol=None):
    log.info("=" * 60)
    log.info("ARGUS-Trader TRAIN")
    log.info("=" * 60)

    data = prepare(symbol=symbol)
    if data is None:
        log.error("no data to train")
        return None

    if data["n_train"] < MIN_SAMPLES:
        log.warning(
            "not enough train samples: %d < %d",
            data["n_train"], MIN_SAMPLES,
        )
        log.warning("train anyway (will be less accurate)")

    X_train = data["X_train"]
    y_train = data["y_train"]
    X_test = data["X_test"]
    y_test = data["y_test"]

    log.info(
        "train: %d samples, test: %d samples",
        len(X_train), len(X_test),
    )

    train_set = lgb.Dataset(
        X_train, label=y_train,
    )
    valid_set = lgb.Dataset(
        X_test, label=y_test, reference=train_set,
    )

    log.info("training...")
    model = lgb.train(
        PARAMS,
        train_set,
        num_boost_round=NUM_ROUNDS,
        valid_sets=[valid_set],
        callbacks=[
            lgb.early_stopping(EARLY_STOP),
            lgb.log_evaluation(50),
        ],
    )

    # Метрики
    y_pred_prob = model.predict(X_test)
    y_pred = (y_pred_prob > 0.5).astype(int)
    acc = float((y_pred == y_test).mean())

    log.info("test accuracy: %.4f", acc)

    # Feature importance
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

    # Save model
    model.save_model(str(MODEL_FILE))
    log.info("saved model: %s", MODEL_FILE.name)

    meta = {
        "trained_at": datetime.now(
            timezone.utc
        ).isoformat(),
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