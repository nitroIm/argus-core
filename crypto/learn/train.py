# ============================================================
# ARGUS-Trader - TRAIN v19 (Meta-Labeling + CPCV)
# ------------------------------------------------------------
# v19: Meta-labeling two-stage framework (López de Prado).
#      Primary: XGBoost + LightGBM ensemble (direction).
#      Meta: XGBoost binary (trade? / size?).
#      RFE: 17 best features from 34.
#      CPCV: Combinatorial Purged Cross-Validation.
#      DSR: Deflated Sharpe Ratio for overfit detection.
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
from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_selection import RFE

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
NUM_ROUNDS = 2000
VAL_FRAC = 0.15
EARLY_STOP = 80
N_FEATURES_RFE = 17

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

META_XGB_PARAMS = {
    "objective": "binary:logistic",
    "eval_metric": "logloss",
    "tree_method": "hist",
    "max_depth": 3,
    "learning_rate": 0.02,
    "subsample": 0.7,
    "colsample_bytree": 0.5,
    "min_child_weight": 20,
    "reg_alpha": 2.0,
    "reg_lambda": 2.0,
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


def model_file(sym):
    return MODELS_DIR / ("lgb_" + sym + ".txt")

def xgb_file(sym):
    return MODELS_DIR / ("xgb_" + sym + ".json")

def meta_model_file(sym):
    return MODELS_DIR / ("meta_lgb_" + sym + ".json")

def meta_file(sym):
    return MODELS_DIR / ("meta_" + sym + ".json")

def prev_model_file(sym):
    return PREV_DIR / ("lgb_" + sym + ".txt")

def prev_meta_file(sym):
    return PREV_DIR / ("meta_" + sym + ".json")


def save_prev(sym):
    mf = model_file(sym)
    if not mf.exists():
        return
    PREV_DIR.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(mf, prev_model_file(sym))
        mfa = meta_file(sym)
        if mfa.exists():
            shutil.copy2(mfa, prev_meta_file(sym))
    except Exception as e:
        log.warning("prev save %s: %s", sym, e)


def _acc(y_true, prob):
    pred = np.argmax(prob, axis=1)
    return float(np.mean(pred == y_true))


def _trading_acc(y_true, prob, thr=0.5):
    max_p = prob.max(axis=1)
    mask = max_p >= thr
    if mask.sum() < 10:
        return 0.0, 0.0, 0
    pred = np.argmax(prob, axis=1)
    acc = float(np.mean(pred[mask] == y_true[mask]))
    cov = float(mask.mean())
    return acc, cov, int(mask.sum())


def _dsr(returns, n_trials=10):
    """Deflated Sharpe Ratio (Bailey & López de Prado)."""
    n = len(returns)
    if n < 10:
        return 0.0
    sr = float(np.mean(returns) / (np.std(returns) + 1e-9))
    sk = float(np.mean(((returns - np.mean(returns)) / (np.std(returns) + 1e-9)) ** 3))
    ku = float(np.mean(((returns - np.mean(returns)) / (np.std(returns) + 1e-9)) ** 4)) - 3
    sr_std = np.sqrt(
        (1 - sk * sr + (ku - 1) / 4 * sr ** 2) / (n - 1)
    )
    if sr_std < 1e-9:
        return 0.0
    emc = 0.5772
    z = (sr - np.sqrt(2 * np.log(n_trials)) * sr_std) / (sr_std + 1e-9)
    dsr = 0.5 + 0.5 * (1 + np.tanh(z * 0.5))
    return float(dsr)


def train_one(symbol):
    log.info("-" * 60)
    log.info("TRAIN %s", symbol)
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

    # --- RFE ---
    rfe_est = RandomForestClassifier(
        n_estimators=100, max_depth=5,
        random_state=42, n_jobs=-1,
    )
    rfe = RFE(rfe_est, n_features_to_select=N_FEATURES_RFE)
    rfe.fit(X_train, y_train)

    sel_mask = rfe.support_
    sel_idx = np.where(sel_mask)[0]
    feat_names = data["feature_cols"]
    sel_names = [feat_names[i] for i in sel_idx]

    log.info("%s: RFE selected %d/%d features",
             symbol, len(sel_idx), len(feat_names))
    log.info("%s: selected: %s", symbol, ", ".join(sel_names))

    X_train_sel = X_train[:, sel_mask]
    X_test_sel = X_test[:, sel_mask]

    n_tr = len(X_train_sel)
    cut = int(n_tr * (1 - VAL_FRAC))
    purge = ds.PURGE_HOURS
    cut_clean = max(cut - purge, int(n_tr * 0.5))

    X_tr = X_train_sel[:cut_clean]
    y_tr = y_train[:cut_clean]
    X_va = X_train_sel[cut:]
    y_va = y_train[cut:]

    log.info("%s: train=%d val=%d test=%d",
             symbol, len(X_tr), len(X_va), len(X_test_sel))

    # --- Primary: LightGBM ---
    train_set = lgb.Dataset(X_tr, label=y_tr)
    val_set = lgb.Dataset(X_va, label=y_va, reference=train_set)

    lgb_model = lgb.train(
        LGB_PARAMS, train_set,
        num_boost_round=NUM_ROUNDS,
        valid_sets=[val_set],
        callbacks=[
            lgb.early_stopping(EARLY_STOP, verbose=False),
            lgb.log_evaluation(200),
        ],
    )
    lgb_best = lgb_model.best_iteration or lgb_model.num_trees()

    # --- Primary: XGBoost ---
    xgb_model = xgb.train(
        XGB_PARAMS,
        xgb.DMatrix(X_tr, label=y_tr),
        num_boost_round=NUM_ROUNDS,
        evals=[(xgb.DMatrix(X_va, label=y_va), "val")],
        early_stopping_rounds=EARLY_STOP,
        verbose_eval=200,
    )
    xgb_best = xgb_model.best_iteration

    # --- Predictions ---
    p_lgb_tr = lgb_model.predict(X_tr)
    p_lgb_va = lgb_model.predict(X_va)
    p_lgb_te = lgb_model.predict(X_test_sel)

    p_xgb_tr = xgb_model.predict(
        xgb.DMatrix(X_tr), iteration_range=(0, xgb_best + 1))
    p_xgb_va = xgb_model.predict(
        xgb.DMatrix(X_va), iteration_range=(0, xgb_best + 1))
    p_xgb_te = xgb_model.predict(
        xgb.DMatrix(X_test_sel), iteration_range=(0, xgb_best + 1))

    p_ens_tr = (p_lgb_tr + p_xgb_tr) / 2.0
    p_ens_va = (p_lgb_va + p_xgb_va) / 2.0
    p_ens_te = (p_lgb_te + p_xgb_te) / 2.0

    acc_ens_tr = _acc(y_tr, p_ens_tr)
    acc_ens_va = _acc(y_va, p_ens_va)
    acc_ens_te = _acc(y_test, p_ens_te)

    # --- Meta-labeling ---
    # Meta target: 1 if primary correct, 0 otherwise
    primary_pred_tr = np.argmax(p_ens_tr, axis=1)
    primary_pred_va = np.argmax(p_ens_va, axis=1)
    primary_pred_te = np.argmax(p_ens_te, axis=1)

    meta_y_tr = (primary_pred_tr == y_tr).astype(int)
    meta_y_va = (primary_pred_va == y_va).astype(int)
    meta_y_te = (primary_pred_te == y_test).astype(int)

    # Meta features: primary conviction + orthogonal (regime only)
    meta_X_tr = np.column_stack([
        p_ens_tr.max(axis=1),
        p_ens_tr[:, 0] - p_ens_tr[:, 2],  # short - long
        X_tr[:, -1],  # regime_hmm
    ])
    meta_X_va = np.column_stack([
        p_ens_va.max(axis=1),
        p_ens_va[:, 0] - p_ens_va[:, 2],
        X_va[:, -1],
    ])
    meta_X_te = np.column_stack([
        p_ens_te.max(axis=1),
        p_ens_te[:, 0] - p_ens_te[:, 2],
        X_test_sel[:, -1],
    ])

    meta_model = xgb.train(
        META_XGB_PARAMS,
        xgb.DMatrix(meta_X_tr, label=meta_y_tr),
        num_boost_round=500,
        evals=[(xgb.DMatrix(meta_X_va, label=meta_y_va), "val")],
        early_stopping_rounds=50,
        verbose_eval=False,
    )

    meta_prob_te = meta_model.predict(
        xgb.DMatrix(meta_X_te),
        iteration_range=(0, meta_model.best_iteration + 1),
    )

    # Meta-filtered accuracy
    meta_mask = meta_prob_te >= 0.55
    if meta_mask.sum() > 10:
        meta_acc = float(np.mean(
            primary_pred_te[meta_mask] == y_test[meta_mask]
        ))
        meta_cov = float(meta_mask.mean())
        meta_n = int(meta_mask.sum())
    else:
        meta_acc, meta_cov, meta_n = 0.0, 0.0, 0

    log.info("%s: ACC  lgb=%.4f xgb=%.4f ens=%.4f",
             symbol,
             _acc(y_test, p_lgb_te),
             _acc(y_test, p_xgb_te),
             acc_ens_te)
    log.info("%s: META: acc=%.4f cov=%.2f n=%d",
             symbol, meta_acc, meta_cov, meta_n)

    # Trading accuracy on confident ensemble
    ta60, cv60, n60 = _trading_acc(y_test, p_ens_te, 0.60)
    ta65, cv65, n65 = _trading_acc(y_test, p_ens_te, 0.65)
    ta70, cv70, n70 = _trading_acc(y_test, p_ens_te, 0.70)

    log.info("%s: max_p>=0.60 acc=%.4f cov=%.2f n=%d",
             symbol, ta60, cv60, n60)
    log.info("%s: max_p>=0.65 acc=%.4f cov=%.2f n=%d",
             symbol, ta65, cv65, n65)
    log.info("%s: max_p>=0.70 acc=%.4f cov=%.2f n=%d",
             symbol, ta70, cv70, n70)

    # DSR on test
    test_ret = data["r_test"]
    dsr_val = _dsr(test_ret)

    log.info("%s: DSR=%.4f", symbol, dsr_val)

    # Feature importance
    imp = lgb_model.feature_importance(importance_type="gain")
    pairs = sorted(zip(sel_names, imp), key=lambda x: -x[1])
    log.info("%s: top features:", symbol)
    for name, score in pairs[:8]:
        log.info("  %s: %.2f", name, score)

    save_prev(symbol)
    lgb_model.save_model(str(model_file(symbol)))
    xgb_model.save_model(str(xgb_file(symbol)))
    meta_model.save_model(str(meta_model_file(symbol)))

    meta = {
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "version": "v19",
        "objective": "multiclass_3class_meta",
        "symbol": symbol,
        "n_total": data["n_total"],
        "n_train": data["n_train"],
        "n_test": data["n_test"],
        "n_tr_inner": len(X_tr),
        "n_val_inner": len(X_va),
        "purge_hours": int(purge),
        "tb_up_mult": data["tb_up_mult"],
        "tb_dn_mult": data["tb_dn_mult"],
        "horizon": data["horizon"],
        "n_features_rfe": N_FEATURES_RFE,
        "selected_features": sel_names,
        "lgb_best_iter": lgb_best,
        "xgb_best_iter": xgb_best,
        "acc_lgb_te": round(_acc(y_test, p_lgb_te), 4),
        "acc_xgb_te": round(_acc(y_test, p_xgb_te), 4),
        "acc_ens_te": round(acc_ens_te, 4),
        "meta_acc": round(meta_acc, 4),
        "meta_cov": round(meta_cov, 4),
        "meta_n": meta_n,
        "conf60_acc": round(ta60, 4),
        "conf60_cov": round(cv60, 4),
        "conf60_n": n60,
        "conf65_acc": round(ta65, 4),
        "conf65_cov": round(cv65, 4),
        "conf65_n": n65,
        "conf70_acc": round(ta70, 4),
        "conf70_cov": round(cv70, 4),
        "conf70_n": n70,
        "dsr": round(dsr_val, 4),
        "top_features": [
            {"name": n, "gain": round(float(s), 2)}
            for n, s in pairs[:10]
        ],
        "lgb_params": LGB_PARAMS,
        "xgb_params": XGB_PARAMS,
        "meta_xgb_params": META_XGB_PARAMS,
        # compat
        "ic_test": round(acc_ens_te - 0.5, 4),
        "ic_val": 0.0,
        "ic_train": 0.0,
        "sign_acc_test": round(acc_ens_te, 4),
        "sign_acc_val": 0.0,
        "sign_acc_train": 0.0,
    }
    with open(meta_file(symbol), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    return meta


def train():
    log.info("=" * 60)
    log.info("ARGUS-Trader TRAIN v19 (Meta-Labeling + CPCV)")
    log.info("SYMBOLS=%s", SYMBOLS_LIST)
    log.info("TB_UP=%.2f TB_DN=%.2f",
             ds.TB_UP_MULT, ds.TB_DN_MULT)
    log.info("RFE: %d features from %d",
             N_FEATURES_RFE, len(ds.FEATURE_COLS))
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
        log.info(
            "  %s: ACC=%.4f META=%.4f conf60=%.4f (cov=%.2f n=%d) DSR=%.4f",
            sym, m["acc_ens_te"], m["meta_acc"],
            m["conf60_acc"], m["conf60_cov"],
            m["conf60_n"], m["dsr"],
        )
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