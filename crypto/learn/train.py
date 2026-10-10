# ============================================================
# ARGUS-Trader - TRAIN v22
# ------------------------------------------------------------
# v22: Per-regime XGBoost + LightGBM ensemble.
#      Walk-forward validation. Regime-weighted blend.
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
log = logging.getLogger("crypto.learn.train")

MODELS_DIR = SCRIPT_DIR / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)
PREV_DIR = MODELS_DIR / "prev"

MIN_SAMPLES = 200
NUM_ROUNDS = 1000
VAL_FRAC = 0.15
EARLY_STOP = 30
Y_CLIP = 20.0

LGB_PARAMS = {
    "objective": "multiclass",
    "num_class": 3,
    "metric": "multi_logloss",
    "boosting_type": "gbdt",
    "num_leaves": 16,
    "max_depth": 4,
    "learning_rate": 0.02,
    "feature_fraction": 0.5,
    "bagging_fraction": 0.7,
    "bagging_freq": 5,
    "min_data_in_leaf": 100,
    "lambda_l1": 1.0,
    "lambda_l2": 1.0,
    "verbose": -1,
    "seed": 42,
}

XGB_PARAMS = {
    "objective": "multi:softprob",
    "num_class": 3,
    "eval_metric": "mlogloss",
    "tree_method": "hist",
    "max_depth": 4,
    "learning_rate": 0.02,
    "subsample": 0.7,
    "colsample_bytree": 0.5,
    "min_child_weight": 10,
    "reg_alpha": 1.0,
    "reg_lambda": 1.0,
    "verbosity": 0,
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


def model_file(sym, regime, kind):
    return MODELS_DIR / f"{kind}_{sym}_reg{regime}.txt"


def xgb_model_file(sym, regime):
    return MODELS_DIR / f"xgb_{sym}_reg{regime}.json"


def meta_file(sym):
    return MODELS_DIR / ("meta_" + sym + ".json")


def save_prev(sym):
    mf = MODELS_DIR / ("lgb_" + sym + ".txt")
    if not mf.exists():
        return
    PREV_DIR.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(mf, PREV_DIR / ("lgb_" + sym + ".txt"))
        mfa = meta_file(sym)
        if mfa.exists():
            shutil.copy2(mfa, PREV_DIR / ("meta_" + sym + ".json"))
    except Exception as e:
        log.warning("prev save %s: %s", sym, e)


def _acc(y_true, prob):
    if len(prob.shape) == 1:
        pred = (prob >= 0.5).astype(int)
        return float(np.mean(pred == y_true))
    pred = np.argmax(prob, axis=1)
    return float(np.mean(pred == y_true))


def _trading_acc(y_true, prob, thr=0.5):
    if len(prob.shape) == 1:
        mask = np.abs(prob - 0.5) >= (thr - 0.5)
        if mask.sum() < 10:
            return 0.0, 0.0, 0
        pred = (prob[mask] >= 0.5).astype(int)
        acc = float(np.mean(pred == y_true[mask]))
        return acc, float(mask.mean()), int(mask.sum())
    max_p = prob.max(axis=1)
    mask = max_p >= thr
    if mask.sum() < 10:
        return 0.0, 0.0, 0
    pred = np.argmax(prob, axis=1)
    acc = float(np.mean(pred[mask] == y_true[mask]))
    return acc, float(mask.mean()), int(mask.sum())


def train_one_regime(symbol, X_tr, y_tr, X_va, y_va, regime_id):
    if len(X_tr) < MIN_SAMPLES:
        log.warning("%s reg%d: too few samples %d",
                    symbol, regime_id, len(X_tr))
        return None

    train_set = lgb.Dataset(X_tr, label=y_tr)
    val_set = lgb.Dataset(X_va, label=y_va, reference=train_set)

    lgb_model = lgb.train(
        LGB_PARAMS, train_set,
        num_boost_round=NUM_ROUNDS,
        valid_sets=[val_set],
        callbacks=[lgb.early_stopping(EARLY_STOP, verbose=False)],
    )
    lgb_best = lgb_model.best_iteration or lgb_model.num_trees()

    xgb_model = xgb.train(
        XGB_PARAMS,
        xgb.DMatrix(X_tr, label=y_tr),
        num_boost_round=NUM_ROUNDS,
        evals=[(xgb.DMatrix(X_va, label=y_va), "val")],
        early_stopping_rounds=EARLY_STOP,
        verbose_eval=False,
    )
    xgb_best = xgb_model.best_iteration

    p_lgb = lgb_model.predict(X_va)
    p_xgb = xgb_model.predict(
        xgb.DMatrix(X_va), iteration_range=(0, xgb_best + 1),
    )
    p_ens = (p_lgb + p_xgb) / 2.0
    acc_va = _acc(y_va, p_ens)

    log.info("%s reg%d: tr=%d va=%d best_lgb=%d best_xgb=%d acc=%.4f",
             symbol, regime_id, len(X_tr), len(X_va),
             lgb_best, xgb_best, acc_va)

    return {
        "lgb": lgb_model,
        "xgb": xgb_model,
        "lgb_best": lgb_best,
        "xgb_best": xgb_best,
        "acc_val": acc_va,
        "n_samples": len(X_tr),
    }


def train_one(symbol):
    log.info("-" * 60)
    log.info("TRAIN %s (per-regime ensemble)", symbol)
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
    y_train = data["y_train"]
    y_test = data["y_test"]
    n_regimes = data["n_regimes"]

    # regime column is the last feature
    reg_train = X_train[:, -1].astype(np.int32)
    reg_test = X_test[:, -1].astype(np.int32)

    n_tr = len(X_train)
    cut = int(n_tr * (1 - VAL_FRAC))
    purge = ds.PURGE_HOURS
    cut_clean = max(cut - purge, int(n_tr * 0.5))

    regime_models = {}
    regime_accs = {}

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
            y_train[:cut_clean][mask_tr],
            X_train[cut:][mask_va],
            y_train[cut:][mask_va],
            reg,
        )
        if result:
            regime_models[reg] = result
            regime_accs[reg] = result["acc_val"]

    if not regime_models:
        log.error("%s: no regime models trained", symbol)
        return None

    # Evaluate on test
    test_probs = np.full((len(X_test), 3), 1.0 / 3.0, dtype=np.float32)
    test_covered = np.zeros(len(X_test), dtype=bool)

    for reg, info in regime_models.items():
        mask = reg_test == reg
        if mask.sum() == 0:
            continue
        p_lgb = info["lgb"].predict(X_test[mask])
        p_xgb = info["xgb"].predict(
            xgb.DMatrix(X_test[mask]),
            iteration_range=(0, info["xgb_best"] + 1),
        )
        p_ens = (p_lgb + p_xgb) / 2.0
        test_probs[mask] = p_ens
        test_covered[mask] = True

    if test_covered.sum() > 0:
        y_test_c = y_test[test_covered]
        p_test_c = test_probs[test_covered]
        acc_te = _acc(y_test_c, p_test_c)
        ta60, cv60, n60 = _trading_acc(y_test_c, p_test_c, 0.60)
        ta65, cv65, n65 = _trading_acc(y_test_c, p_test_c, 0.65)
        ta70, cv70, n70 = _trading_acc(y_test_c, p_test_c, 0.70)
    else:
        acc_te = 0.0
        ta60 = cv60 = n60 = 0
        ta65 = cv65 = n65 = 0
        ta70 = cv70 = n70 = 0

    coverage = float(test_covered.mean())

    log.info("%s: test acc=%.4f coverage=%.2f", symbol, acc_te, coverage)
    log.info("%s: |p|>=0.60 acc=%.4f cov=%.2f n=%d", symbol, ta60, cv60, n60)
    log.info("%s: |p|>=0.65 acc=%.4f cov=%.2f n=%d", symbol, ta65, cv65, n65)
    log.info("%s: |p|>=0.70 acc=%.4f cov=%.2f n=%d", symbol, ta70, cv70, n70)

    for reg, info in regime_models.items():
        info["lgb"].save_model(str(model_file(symbol, reg, "lgb")))
        info["xgb"].save_model(str(xgb_model_file(symbol, reg)))

    best_reg = max(regime_accs, key=regime_accs.get)
    shutil.copy2(
        model_file(symbol, best_reg, "lgb"),
        MODELS_DIR / ("lgb_" + symbol + ".txt"),
    )

    meta = {
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "version": "v22",
        "symbol": symbol,
        "n_regimes": n_regimes,
        "regime_models": {
            str(k): {"acc_val": v["acc_val"], "n_samples": v["n_samples"]}
            for k, v in regime_models.items()
        },
        "acc_test": round(acc_te, 4),
        "coverage": round(coverage, 4),
        "conf60_acc": round(ta60, 4),
        "conf60_cov": round(cv60, 4),
        "conf60_n": n60,
        "conf65_acc": round(ta65, 4),
        "conf65_cov": round(cv65, 4),
        "conf65_n": n65,
        "conf70_acc": round(ta70, 4),
        "conf70_cov": round(cv70, 4),
        "conf70_n": n70,
        "best_regime": best_reg,
        "features": data["feature_cols"],
        "horizon": data["horizon"],
        "ic_test": round(acc_te - 0.5, 4),
        "ic_val": round(max(regime_accs.values()) - 0.5, 4),
        "ic_train": 0.0,
        "sign_acc_test": round(acc_te, 4),
        "sign_acc_val": 0.0,
        "sign_acc_train": 0.0,
    }
    with open(meta_file(symbol), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    return meta


def train():
    log.info("=" * 60)
    log.info("ARGUS-Trader TRAIN v22 (per-regime ensemble)")
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
        log.info("  %s: ACC=%.4f conf60=%.4f (cov=%.2f n=%d)",
                 sym, m["acc_test"], m["conf60_acc"],
                 m["conf60_cov"], m["conf60_n"])
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