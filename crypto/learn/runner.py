# ============================================================
# ARGUS-Trader - LEARN RUNNER
# ------------------------------------------------------------
# v5: REGRESSION_VERSIONS = {v12, v13}.
# v4: version-aware skip for regression models.
# v3: + STEP 5 learn_weights.
# v2: skip train if model fresh (<24h).
# ============================================================

import sys
import json
import logging
from pathlib import Path
from datetime import datetime, timezone

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import train

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.learn.runner")

MODEL_META = SCRIPT_DIR / "models" / "model_meta.json"
MODEL_MAX_AGE_H = 24

REGRESSION_VERSIONS = {"v12", "v13"}


def read_meta():
    if not MODEL_META.exists():
        log.warning(
            "meta missing: %s", MODEL_META.name
        )
        return None
    try:
        with open(
            MODEL_META, "r", encoding="utf-8"
        ) as f:
            return json.load(f)
    except Exception as e:
        log.warning("meta read: %s", e)
        return None


def model_is_fresh(meta):
    if not meta:
        return False

    ts = meta.get("trained_at")
    if not ts:
        log.warning("meta has no trained_at")
        return False

    try:
        trained = datetime.fromisoformat(ts)
    except Exception as e:
        log.warning("trained_at parse: %s", e)
        return False

    if trained.tzinfo is None:
        trained = trained.replace(
            tzinfo=timezone.utc
        )

    age_h = (
        datetime.now(timezone.utc) - trained
    ).total_seconds() / 3600
    log.info(
        "model age: %.1fh (max %dh), version=%s",
        age_h, MODEL_MAX_AGE_H,
        meta.get("version", "?"),
    )
    return age_h < MODEL_MAX_AGE_H


def is_regression(meta):
    if not meta:
        return False
    obj = str(
        meta.get("objective", "")
    ).strip().lower()
    if obj == "regression":
        return True
    v = str(meta.get("version", "")).strip()
    return v in REGRESSION_VERSIONS


def main():
    log.info("=" * 60)
    log.info(
        "ARGUS-Trader LEARN RUNNER - %s",
        datetime.now(timezone.utc).isoformat(),
    )
    log.info("=" * 60)

    meta = read_meta()
    fresh = model_is_fresh(meta)

    if fresh:
        log.info("model fresh -> skip train")
    else:
        log.info("STEP 1: train")
        metas = train.train()
        if not metas:
            log.error("train failed, stop")
            return
        meta = read_meta()
        if meta:
            log.info(
                "trained %d models, version=%s",
                len(metas),
                meta.get("version", "?"),
            )

    if is_regression(meta):
        log.warning(
            "STEP 2-5: SKIPPED - regression model "
            "(%s). evaluate/predict/signals not "
            "adapted yet.",
            meta.get("version"),
        )
        log.info("=" * 60)
        log.info("LEARN DONE (train-only)")
        log.info("=" * 60)
        return

    log.info("STEP 2: evaluate")
    import evaluate
    m = evaluate.evaluate()
    if m:
        log.info(
            "eval accuracy=%.4f f1=%.4f",
            m["accuracy"], m["f1"],
        )

    log.info("STEP 3: predict")
    import predict
    predict.main()

    log.info("STEP 4: signals")
    import signals
    signals.main()

    log.info("STEP 5: learn weights")
    try:
        import learn_weights
        learn_weights.main()
    except Exception as e:
        log.warning(
            "weights update skipped: %s", e
        )

    log.info("=" * 60)
    log.info("LEARN DONE")
    log.info("=" * 60)


if __name__ == "__main__":
    main()