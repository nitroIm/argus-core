# ============================================================
# ARGUS-Trader - AUTOTUNE v4 [PRODUCTION]
# ------------------------------------------------------------
# v4: regression — objective='regression', metric='rmse'.
#     Grid evaluated by test IC (Spearman), not accuracy.
#     Saves to autotune_results.json (not best_params.json)
#     because train.py v13 uses hardcoded PARAMS.
#     Removed MIN_EDGE unlink (dangerous auto-delete).
#     Reads meta.objective to detect model type.
# v3: save best_params only if edge > MIN_EDGE.
# v2: fix make_params.
# ============================================================

import sys
import os
os.environ.setdefault("USE_CROSS", "0")

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
RESULTS_FILE = MODELS_DIR / "autotune_results.json"

GRID = {
    "num_leaves": [8, 15, 31],
    "learning_rate": [0.03, 0.05, 0.08],
    "max_depth": [3, 5],
}

NUM_ROUNDS = 500
EARLY_STOP = 30
VAL_FRAC = 0.15


def make_params(num_leaves, learning_rate,
                max_depth):
    return {
        "objective": "regression",
        "metric": "rmse",
        "boosting_type": "gbdt",
        "num_leaves": num_leaves,
        "max_depth": max_depth,
        "learning_rate": learning_rate,
        "feature_fraction": 0.5,
        "bagging_fraction": 0.6,
        "bagging_freq": 5,
        "min_data_in_leaf": 80,
        "lambda_l1": 1.0,
        "lambda_l2": 1.0,
        "verbose": -1,
        "seed": 42,
    }


def _ic(y_true, y_pred):
    if len(y_true) < 10:
        return 0.0
    if np.std(y_true) == 0 or np.std(y_pred) == 0:
        return 0.0
    yt = y_true - y_true.mean()
    yp = y_pred - y_pred.mean()
    d = np.sqrt((yt * yt).sum() * (yp * yp).sum())
    if d == 0:
        return 0.0
    return float((yt * yp).sum() / d)


def evaluate_params(params, X_train, r_train,
                    X_test, r_test):
    n_tr = len(X_train)
    cut = int(n_tr * (1 - VAL_FRAC))
    X_tr = X_train[:cut]
    r_tr = r_train[:cut]
    X_va = X_train[cut:]
    r_va = r_train[cut:]

    train_set = lgb.Dataset(X_tr, label=r_tr)
    val_set = lgb.Dataset(
        X_va, label=r_va,
        reference=train_set,
    )

    try:
        model = lgb.train(
            params, train_set,
            num_boost_round=NUM_ROUNDS,
            valid_sets=[val_set],
            callbacks=[
                lgb.early_stopping(
                    EARLY_STOP, verbose=False,
                ),
            ],
        )
    except Exception as e:
        log.warning("train fail: %s", e)
        return None

    p_test = model.predict(X_test).astype(
        np.float32
    )
    ic_te = _ic(r_test, p_test)
    mae_te = float(
        np.mean(np.abs(p_test - r_test))
    )
    rmse_te = float(
        np.sqrt(np.mean((p_test - r_test) ** 2))
    )
    best_iter = (
        model.best_iteration or model.num_trees()
    )

    return {
        "ic_test": round(ic_te, 4),
        "mae_test": round(mae_te, 4),
        "rmse_test": round(rmse_te, 4),
        "best_iter": int(best_iter),
    }


def autotune():
    log.info("=" * 60)
    log.info("ARGUS AUTOTUNE v4 (regression)")
    log.info("=" * 60)

    data = prepare()
    if data is None:
        log.error("no data")
        return None

    X_train = data["X_train"]
    X_test = data["X_test"]
    r_train = data["r_train"]
    r_test = data["r_test"]

    log.info(
        "train=%d test=%d features=%d",
        len(X_train), len(X_test),
        len(data["feature_cols"]),
    )

    keys = list(GRID.keys())
    values = list(GRID.values())
    combos = list(itertools.product(*values))

    log.info(
        "testing %d combinations", len(combos),
    )

    results = []
    for i, combo in enumerate(combos, 1):
        cfg = dict(zip(keys, combo))
        params = make_params(**cfg)

        r = evaluate_params(
            params, X_train, r_train,
            X_test, r_test,
        )
        if r is None:
            continue

        r["params"] = cfg
        results.append(r)
        log.info(
            "[%d/%d] %s -> IC=%+.4f "
            "RMSE=%.4f iter=%d",
            i, len(combos), cfg,
            r["ic_test"], r["rmse_test"],
            r["best_iter"],
        )

    if not results:
        log.error("no results")
        return None

    best = max(
        results, key=lambda x: x["ic_test"],
    )
    log.info("=" * 60)
    log.info("BEST: %s", best["params"])
    log.info(
        "IC=%+.4f RMSE=%.4f iter=%d",
        best["ic_test"], best["rmse_test"],
        best["best_iter"],
    )
    log.info("=" * 60)

    out = {
        "tuned_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "version": "v4",
        "objective": "regression",
        "n_train": len(X_train),
        "n_test": len(X_test),
        "n_features": len(data["feature_cols"]),
        "best": best,
        "all_results": results,
    }

    with open(
        RESULTS_FILE, "w", encoding="utf-8",
    ) as f:
        json.dump(
            out, f,
            ensure_ascii=False, indent=2,
        )

    log.info(
        "saved: %s", RESULTS_FILE.name
    )
    return best


def main():
    autotune()


if __name__ == "__main__":
    main()