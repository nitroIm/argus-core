# ============================================================
# ARGUS-Trader - TRAIN v21
# ------------------------------------------------------------
# v21: per-regime models (one LightGBM per regime).
#      FIX: reg_train/reg_test -> np.asarray for bool masks.
#      Regime detector from dataset v24 (HMM).
# ============================================================

import os
os.environ.setdefault("USE_CROSS", "0")

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

import dataset as ds

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.learn.train")

MODELS_DIR = SCRIPT_DIR / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)
PREV_DIR = MODELS_DIR / "prev"

MIN_SAMPLES = 200
NUM_ROUNDS = 1000
VAL_FRAC = 0.15
EARLY_STOP = 30
Y_CLIP = 20.0

PARAMS_BASE = {
    "objective": "regression",
    "metric": "rmse",
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

SYMBOLS_LIST = [
    s.strip().upper()
    for s in (os.getenv("SYMBOLS")
              or "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT").split(",")
    if s.strip()
]
DB2_SET = {
    s.strip().upper()
    for s in (os.getenv("DB2_SYMBOLS")
              or "SOLUSDT,BNBUSDT").split(",")
    if s.strip()
}


def model_file(sym, regime):
    return MODELS_DIR / f"lgb_{sym}_reg{regime}.txt"


def meta_file(sym):
    return MODELS_DIR / ("meta_" + sym + ".json")


def prev_model_file(sym):
    return PREV_DIR / ("lgb_" + sym + ".txt")


def save_prev(sym):
    mf = MODELS_DIR / ("lgb_" + sym + ".txt")
    if not mf.exists():
        return
    PREV_DIR.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(mf, prev_model_file(sym))
        mfa = meta_file(sym)
        if mfa.exists():
            shutil.copy2(mfa, PREV_DIR / ("meta_" + sym + ".json"))
    except Exception as e:
        log.warning("prev save %s: %s", sym, e)


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


def train_one_regime(symbol, X_tr, y_tr, X_va, y_va, regime_id):
    if len(X_tr) < MIN_SAMPLES:
        log.warning("%s reg%d: too few samples %d",
                    symbol, regime_id, len(X_tr))
        return None

    train_set = lgb.Dataset(X_tr, label=y_tr)
    val_set = lgb.Dataset(X_va, label=y_va, reference=train_set)

    model = lgb.train(
        PARAMS_BASE, train_set,
        num_boost_round=NUM_ROUNDS,
        valid_sets=[val_set],
        callbacks=[
            lgb.early_stopping(EARLY_STOP, verbose=False),
        ],
    )

    best_iter = model.best_iteration or model.num_trees()
    p_te = model.predict(X_va).astype(np.float32)
    ic_te = _ic(y_va, p_te)

    log.info("%s reg%d: samples=%d best_iter=%d IC_val=%.4f",
             symbol, regime_id, len(X_tr), best_iter, ic_te)

    return {
        "model": model,
        "best_iter": best_iter,
        "ic_val": ic_te,
        "n_samples": len(X_tr),
    }


def train_one(symbol):
    log.info("-" * 60)
    log.info("TRAIN %s (per-regime)", symbol)
    log.info("-" * 60)

    ds.SYMBOLS = [symbol]
    ds.REFERENCE = symbol
    ds.DB2_SYMBOLS = {symbol} if symbol in DB2_SET else set()

    data = ds.prepare()
    if data is None:
        log.error("%s: no data", symbol)
        return None

    X_train = data["X_train"]
    X_test = data["X_test"]
    r_train = data["r_train"]
    r_test = data["r_test"]
    reg_train = data["reg_train"]
    reg_test = data["reg_test"]
    n_regimes = data["n_regimes"]

    # FIX: list -> np.array for boolean masks
    reg_train = np.asarray(reg_train)
    reg_test = np.asarray(reg_test)

    r_train = np.clip(r_train, -Y_CLIP, Y_CLIP)
    r_test_c = np.clip(r_test, -Y_CLIP, Y_CLIP)

    n_tr = len(X_train)
    cut = int(n_tr * (1 - VAL_FRAC))
    purge = ds.PURGE_HOURS
    cut_clean = max(cut - purge, int(n_tr * 0.5))

    regime_models = {}
    regime_ics = {}

    for reg in range(n_regimes):
        mask_tr = reg_train[:cut_clean] == reg
        mask_va = reg_train[cut:] == reg

        n_reg_tr = int(mask_tr.sum())
        n_reg_va = int(mask_va.sum())

        if n_reg_tr < MIN_SAMPLES or n_reg_va < 50:
            log.warning("%s reg%d: skip (tr=%d va=%d)",
                        symbol, reg, n_reg_tr, n_reg_va)
            continue

        result = train_one_regime(
            symbol,
            X_train[:cut_clean][mask_tr],
            r_train[:cut_clean][mask_tr],
            X_train[cut:][mask_va],
            r_train[cut:][mask_va],
            reg,
        )
        if result:
            regime_models[reg] = result
            regime_ics[reg] = result["ic_val"]

    if not regime_models:
        log.error("%s: no regime models trained", symbol)
        return None

    test_preds = np.zeros(len(X_test), dtype=np.float32)
    test_weights = np.zeros(len(X_test), dtype=np.float32)

    for reg, info in regime_models.items():
        mask = reg_test == reg
        if mask.sum() == 0:
            continue
        p = info["model"].predict(X_test[mask]).astype(np.float32)
        w = max(0.0, info["ic_val"])
        test_preds[mask] = p
        test_weights[mask] = w

    mask_covered = test_weights > 0
    if mask_covered.sum() > 0:
        ic_te = _ic(r_test_c[mask_covered], test_preds[mask_covered])
        acc_te = float(np.mean(
            np.sign(test_preds[mask_covered]) == np.sign(r_test_c[mask_covered])
        ))
    else:
        ic_te = 0.0
        acc_te = 0.0

    coverage = float(mask_covered.mean())

    log.info("%s: test IC=%.4f acc=%.4f coverage=%.2f",
             symbol, ic_te, acc_te, coverage)

    for reg, info in regime_models.items():
        info["model"].save_model(str(model_file(symbol, reg)))
        log.info("%s: saved reg%d (IC_val=%.4f)",
                 symbol, reg, info["ic_val"])

    best_reg = max(regime_ics, key=regime_ics.get)
    shutil.copy2(
        model_file(symbol, best_reg),
        MODELS_DIR / ("lgb_" + symbol + ".txt"),
    )

    meta = {
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "version": "v21",
        "symbol": symbol,
        "n_regimes": n_regimes,
        "regime_models": {
            str(k): {"ic_val": v["ic_val"], "n_samples": v["n_samples"]}
            for k, v in regime_models.items()
        },
        "ic_test": round(ic_te, 4),
        "acc_test": round(acc_te, 4),
        "coverage": round(coverage, 4),
        "best_regime": best_reg,
        "features": data["feature_cols"],
        "horizon": data["horizon"],
    }
    with open(meta_file(symbol), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    return meta


def train():
    log.info("=" * 60)
    log.info("ARGUS-Trader TRAIN v21 (per-regime)")
    log.info("SYMBOLS=%s", SYMBOLS_LIST)
    log.info("N_REGIMES=%d", ds.N_REGIMES)
    log.info("=" * 60)

    metas = {}
    for sym in SYMBOLS_LIST:
        try:
            m = train_one(sym)
            if m:
                metas[sym] = m
        except Exception as e:
            log.error("%s: %s", sym, e)

    log.info("=" * 60)
    log.info("TRAIN DONE")
    for sym, m in metas.items():
        log.info("  %s: IC_te=%.4f coverage=%.2f",
                 sym, m["ic_test"], m["coverage"])
    if metas:
        ic_avg = sum(m["ic_test"] for m in metas.values()) / len(metas)
        log.info("  AVG IC_test=%.4f (%d models)", ic_avg, len(metas))
    log.info("=" * 60)

    return metas


def main():
    metas = train()
    if not metas:
        log.error("train failed")
        return
    log.info("DONE. %d models trained", len(metas))


if __name__ == "__main__":
    main()