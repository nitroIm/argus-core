# ============================================================
# ARGUS-Trader - TRAIN XGB v1
# ------------------------------------------------------------
# v1: XGBoost per-symbol. Same dataset as LightGBM.
#     Saves lgb_{sym}.json -> models/xgb_{sym}.json
#     Does NOT touch LightGBM models.
# ============================================================

import os
os.environ.setdefault("USE_CROSS", "0")

import sys
import json
import logging
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import xgboost as xgb

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
log = logging.getLogger("crypto.learn.train_xgb")

MODELS_DIR = SCRIPT_DIR / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

NUM_ROUNDS = 1000
EARLY_STOP = 30
VAL_FRAC = 0.15

PARAMS = {
    "objective": "reg:squarederror",
    "eval_metric": "rmse",
    "tree_method": "hist",
    "max_depth": 3,
    "learning_rate": 0.03,
    "subsample": 0.6,
    "colsample_bytree": 0.5,
    "min_child_weight": 10,
    "reg_alpha": 1.0,
    "reg_lambda": 1.0,
    "verbosity": 0,
    "seed": 42,
}

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


def train_one(symbol):
    log.info("-" * 60)
    log.info("TRAIN XGB %s", symbol)
    log.info("-" * 60)

    ds.SYMBOLS = [symbol]
    ds.REFERENCE = symbol
    ds.DB2_SYMBOLS = (
        {symbol} if symbol in DB2_SET else set()
    )

    data = ds.prepare()
    if data is None:
        log.error("%s: no data", symbol)
        return None

    X_train = data["X_train"]
    X_test = data["X_test"]
    r_train = data["r_train"]
    r_test = data["r_test"]

    n_tr = len(X_train)
    cut = int(n_tr * (1 - VAL_FRAC))
    X_tr = X_train[:cut]
    r_tr = r_train[:cut]
    X_va = X_train[cut:]
    r_va = r_train[cut:]

    log.info(
        "%s: train=%d val=%d test=%d",
        symbol, len(X_tr), len(X_va), len(X_test),
    )

    dtrain = xgb.DMatrix(X_tr, label=r_tr)
    dval = xgb.DMatrix(X_va, label=r_va)
    dtest = xgb.DMatrix(X_test, label=r_test)

    evals = [(dval, "val")]
    model = xgb.train(
        PARAMS,
        dtrain,
        num_boost_round=NUM_ROUNDS,
        evals=evals,
        early_stopping_rounds=EARLY_STOP,
        verbose_eval=100,
    )

    best_iter = model.best_iteration
    log.info(
        "%s: best_iteration=%d (of %d)",
        symbol, best_iter, NUM_ROUNDS,
    )

    p_train = model.predict(
        xgb.DMatrix(X_train),
        iteration_range=(0, best_iter + 1),
    ).astype(np.float32)
    p_test = model.predict(
        dtest,
        iteration_range=(0, best_iter + 1),
    ).astype(np.float32)

    ic_tr = _ic(r_train, p_train)
    ic_te = _ic(r_test, p_test)
    mae_te = float(np.mean(np.abs(p_test - r_test)))
    rmse_te = float(
        np.sqrt(np.mean((p_test - r_test) ** 2))
    )

    log.info(
        "%s: test: IC=%.4f MAE=%.4f RMSE=%.4f",
        symbol, ic_te, mae_te, rmse_te,
    )
    log.info(
        "%s: train: IC=%.4f",
        symbol, ic_tr,
    )

    imp_dict = model.get_score(importance_type="gain")
    pairs = sorted(
        imp_dict.items(),
        key=lambda x: -x[1],
    )
    log.info("%s: top features:", symbol)
    for name, score in pairs[:5]:
        log.info("  %s: %.2f", name, score)

    model_path = MODELS_DIR / (
        "xgb_" + symbol + ".json"
    )
    model.save_model(str(model_path))
    log.info("saved %s", model_path.name)

    meta = {
        "trained_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "version": "v1-xgb",
        "objective": "regression",
        "algorithm": "xgboost",
        "symbol": symbol,
        "n_total": data["n_total"],
        "n_train": data["n_train"],
        "n_test": data["n_test"],
        "best_iteration": int(best_iter),
        "ic_train": round(ic_tr, 4),
        "ic_test": round(ic_te, 4),
        "mae_test": round(mae_te, 4),
        "rmse_test": round(rmse_te, 4),
        "features": data["feature_cols"],
        "top_features": [
            {"name": n, "gain": round(float(s), 2)}
            for n, s in pairs[:10]
        ],
        "horizon": data["horizon"],
    }
    meta_path = MODELS_DIR / (
        "meta_xgb_" + symbol + ".json"
    )
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    log.info("saved %s", meta_path.name)

    return meta


def train():
    log.info("=" * 60)
    log.info("ARGUS-Trader TRAIN XGB v1")
    log.info("SYMBOLS=%s", SYMBOLS_LIST)
    log.info(
        "NUM_ROUNDS=%d EARLY_STOP=%d",
        NUM_ROUNDS, EARLY_STOP,
    )
    log.info("PARAMS: %s", PARAMS)
    log.info("=" * 60)

    metas = {}
    for sym in SYMBOLS_LIST:
        try:
            m = train_one(sym)
            if m:
                metas[sym] = m
        except Exception as exc:
            log.error("%s: %s", sym, exc)

    log.info("=" * 60)
    log.info("XGB TRAIN DONE")
    for sym, m in metas.items():
        log.info(
            "  %s: IC=%.4f best_iter=%d",
            sym, m["ic_test"],
            m["best_iteration"],
        )
    if metas:
        avg = sum(
            m["ic_test"] for m in metas.values()
        ) / len(metas)
        log.info("  AVG IC=%.4f (%d models)", avg, len(metas))
    log.info("=" * 60)

    ds.SYMBOLS = ["BTCUSDT", "ETHUSDT"]
    ds.REFERENCE = "BTCUSDT"
    ds.DB2_SYMBOLS = DB2_SET

    return metas


def main():
    metas = train()
    if not metas:
        log.error("xgb train failed")
        return
    log.info("DONE. %d xgb models", len(metas))


if __name__ == "__main__":
    main()