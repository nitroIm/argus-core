# ============================================================
# ARGUS-Trader - LEARN RUNNER
# ------------------------------------------------------------
# Оркестратор: train -> evaluate -> predict -> signals.
# ============================================================

import sys
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


def main():
    log.info("=" * 60)
    log.info(
        "ARGUS-Trader LEARN RUNNER - %s",
        datetime.now(timezone.utc).isoformat(),
    )
    log.info("=" * 60)

    # 1. train
    log.info("STEP 1: train")
    meta = train.train()
    if meta is None:
        log.error("train failed, stop")
        return

    # 2. evaluate
    log.info("STEP 2: evaluate")
    m = evaluate.evaluate()
    if m:
        log.info(
            "eval accuracy=%.4f f1=%.4f",
            m["accuracy"], m["f1"],
        )

    # 3. predict
    log.info("STEP 3: predict")
    predict.main()

    # 4. signals
    log.info("STEP 4: signals")
    signals.main()

    log.info("=" * 60)
    log.info("LEARN DONE")
    log.info("=" * 60)


if __name__ == "__main__":
    main()