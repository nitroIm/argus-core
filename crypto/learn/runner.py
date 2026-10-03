# ============================================================
# ARGUS-Trader - LEARN RUNNER
# ------------------------------------------------------------
# Оркестратор: train -> evaluate -> predict -> signals.
# v2: skip train/evaluate if model fresh (<24h).
#     Predict + signals always run.
# ============================================================

import sys
import json
import logging
from pathlib import Path
from datetime import datetime, timezone

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import train
import evaluate
import predict
import signals

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.learn.runner")

MODEL_META = SCRIPT_DIR / "models" / "model_meta.json"
MODEL_MAX_AGE_H = 24


def model_is_fresh():
    """True if trained_at is younger than MODEL_MAX_AGE_H."""
    if not MODEL_META.exists():
        log.warning("meta missing: %s", MODEL_META.name)
        return False
    try:
        with open(MODEL_META, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except Exception as e:
        log.warning("meta read: %s", e)
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
        trained = trained.replace(tzinfo=timezone.utc)

    age_h = (
        datetime.now(timezone.utc) - trained
    ).total_seconds() / 3600
    log.info(
        "model age: %.1fh (max %dh)",
        age_h, MODEL_MAX_AGE_H,
    )
    return age_h < MODEL_MAX_AGE_H


def main():
    log.info("=" * 60)
    log.info(
        "ARGUS-Trader LEARN RUNNER - %s",
        datetime.now(timezone.utc).isoformat(),
    )
    log.info("=" * 60)

    if model_is_fresh():
        log.info("model fresh -> skip train+evaluate")
    else:
        log.info("STEP 1: train")
        meta = train.train()
        if meta is None:
            log.error("train failed, stop")
            return

        log.info("STEP 2: evaluate")
        m = evaluate.evaluate()
        if m:
            log.info(
                "eval accuracy=%.4f f1=%.4f",
                m["accuracy"], m["f1"],
            )

    log.info("STEP 3: predict")
    predict.main()

    log.info("STEP 4: signals")
    signals.main()

    log.info("=" * 60)
    log.info("LEARN DONE")
    log.info("=" * 60)


if __name__ == "__main__":
    main()