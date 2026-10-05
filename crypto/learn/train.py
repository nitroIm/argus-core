# ============================================================
# ARGUS-Trader - TRAIN [PRODUCTION]
# ------------------------------------------------------------
# v9: best_params отключён (устарел на новых данных).
#     Упрощённая модель: num_leaves=8, max_depth=3, lr=0.03.
#     Больше регуляризации.
# v8: убран early_stopping, 100 фикс. итераций.
# v7: убран is_unbalance.
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

MIN_SAMPLES = 200
NUM_ROUNDS = 100

# Простая модель для 11k samples × 40 фич.
# Не грузим best_params — он устарел.
PARAMS = {
    "objective": "binary",
    "metric": "binary_logloss",
    "boosting_type": "gbdt",
    "num_leaves": 8,
    "max_depth": 3,
    "learning_rate": 0.03,
    "feature_fraction": 0.5,
    "bagging_fraction": 0.6,
    "bagging_freq": 5,
    "min_data_in_leaf": 80,
    "lambda_l1": 1.0,
    "lambda_l2": 1.0,
    "verbose": -1,
    "seed": 42,
}


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


def train():
    log.info("=" * 60)
    log.info("ARGUS-Trader TRAIN v9")
    log.info("=" * 60)
    log.info("PARAMS: %s", PARAMS)

    data = prepare()
    if data is None:
        log.error("no data")
        return None

    X_train = data["X_train"]
    y_train = data["y_train"]
    X_test = data["X_test"]
    y_test = data["y_test"]

    log.info(
        "train=%d test=%d",
        len(X_train), len(X_test),
    )
    log.info(
        "train balance: up=%d down=%d (up %.1f%%)",
        int(y_train.sum()),
        int(len(y_train) - y_train.sum()),
        y_train.mean() * 100,
    )
    log.info(
        "test balance:  up=%d down=%d (up %.1f%%)",
        int(y_test.sum()),
        int(len(y_test) - y_test.sum()),
        y_test.mean() * 100,
    )

    train_set = lgb.Dataset(X_train, label=y_train)

    log.info("training (%d rounds)...", NUM_ROUNDS)
    model = lgb.train(
        PARAMS, train_set,
        num_boost_round=NUM_ROUNDS,
        callbacks=[lgb.log_evaluation(50)],
    )

    # --- train diag ---
    y_train_prob = model.predict(X_train)
    y_train_pred = (y_train_prob > 0.5).astype(int)
    train_acc = float((y_train_pred == y_train).mean())
    log.info(
        "train: acc=%.4f pred_up=%.1f%%",
        train_acc, y_train_pred.mean() * 100,
    )

    # --- test diag ---
    y_pred_prob = model.predict(X_test)
    y_pred = (y_pred_prob > 0.5).astype(int)
    acc = float((y_pred == y_test).mean())

    pred_up = int(y_pred.sum())
    log.info(
        "test:  acc=%.4f pred_up=%.1f%%",
        acc, pred_up / len(y_pred) * 100,
    )

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
        "version": "v9",
        "n_total": data["n_total"],
        "n_train": data["n_train"],
        "n_test": data["n_test"],
        "accuracy": round(acc, 4),
        "train_accuracy": round(train_acc, 4),
        "num_trees": model.num_trees(),
        "features": feat_names,
        "top_features": [
            {"name": n, "gain": round(float(s), 2)}
            for n, s in pairs[:10]
        ],
        "balance": data["balance"],
        "train_up_pct": round(
            float(y_train.mean()) * 100, 2
        ),
        "test_up_pct": round(
            float(y_test.mean()) * 100, 2
        ),
        "pred_up_pct": round(
            pred_up / len(y_pred) * 100, 2
        ),
        "symbols": data["symbols"],
        "reference": data["reference"],
        "horizon": data["horizon"],
        "threshold_pct": data["threshold_pct"],
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
        "DONE. train=%.4f test=%.4f trees=%d",
        meta["train_accuracy"], meta["accuracy"],
        meta["num_trees"],
    )
    log.info("=" * 60)


if __name__ == "__main__":
    main()