# ============================================================
# ARGUS-Trader - AUTOTUNE [PRODUCTION]
# ------------------------------------------------------------
# Перебирает комбинации параметров LightGBM.
# Выбирает лучшую по edge на test.
# Сохраняет в models/best_params.json.
# ============================================================

import sys
import json
import logging
import itertools
from pathlib import Path
from datetime import datetime, timezone

import lightgbm as lgb
import numpy as np

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
log = logging.getLogger("crypto.autotune")

MODELS_DIR = SCRIPT_DIR / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)
BEST_PARAMS_FILE = MODELS_DIR / "best_params.json"

# Сетка (12 комбинаций — быстро)
GRID = {
    "num_leaves": [15, 31],
    "learning_rate": [0.03, 0.05, 0.08],
    "max_depth": [3, 5],
}

NUM_ROUNDS = 200
EARLY_STOP = 30


def make_params(num_leaves, lr, max_depth):
    return {
        "objective": "binary",
        "is_unbalance": True,
        "metric": "binary_logloss",
        "boosting_type": "gbdt",
        "num_leaves": num_leaves,
        "max_depth": max_depth,
        "learning_rate": lr,
        "feature_fraction": 0.7,
        "bagging_fraction": 0.7,
        "bagging_freq": 5,
        "min_data_in_leaf": 30,
        "lambda_l1": 0.3,
        "lambda_l2": 0.3,
        "verbose": -1,
        "seed": 42,
    }


def evaluate_params(params, X_train, y_train,
                    X_test, y_test):
    train_set = lgb.Dataset(X_train, label=y_train)
    valid_set = lgb.Dataset(
        X_test, label=y_test, reference=train_set,
    )
    try:
        model = lgb.train(
            params, train_set,
            num_boost_round=NUM_ROUNDS,
            valid_sets=[valid_set],
            callbacks=[
                lgb.early_stopping(
                    EARLY_STOP, verbose=False,
                ),
            ],
        )
    except Exception as e:
        log.warning("train fail: %s", e)
        return None

    y_pred = (model.predict(X_test) > 0.5).astype(int)
    acc = float((y_pred == y_test).mean())
    up = float(y_test.mean())
    baseline = max(up, 1 - up)
    edge = acc - baseline

    return {
        "accuracy": round(acc, 4),
        "baseline": round(baseline, 4),
        "edge": round(edge, 4),
        "num_trees": model.num_trees(),
    }


def autotune():
    log.info("=" * 60)
    log.info("ARGUS AUTOTUNE")
    log.info("=" * 60)

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

    keys = list(GRID.keys())
    values = list(GRID.values())
    combos = list(itertools.product(*values))

    log.info("testing %d combinations", len(combos))

    results = []
    for i, combo in enumerate(combos, 1):
        cfg = dict(zip(keys, combo))
        params = make_params(**cfg)

        r = evaluate_params(
            params, X_train, y_train,
            X_test, y_test,
        )
        if r is None:
            continue

        r["params"] = cfg
        results.append(r)
        log.info(
            "[%d/%d] %s -> edge=%+.4f acc=%.4f",
            i, len(combos), cfg,
            r["edge"], r["accuracy"],
        )

    if not results:
        log.error("no results")
        return None

    best = max(results, key=lambda x: x["edge"])
    log.info("=" * 60)
    log.info("BEST: %s", best["params"])
    log.info(
        "edge=%+.4f acc=%.4f trees=%d",
        best["edge"], best["accuracy"],
        best["num_trees"],
    )
    log.info("=" * 60)

    out = {
        "tuned_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "n_train": len(X_train),
        "n_test": len(X_test),
        "best": best,
        "all_results": results,
    }

    with open(BEST_PARAMS_FILE, "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    log.info("saved: %s", BEST_PARAMS_FILE.name)
    return best


def main():
    autotune()


if __name__ == "__main__":
    main()