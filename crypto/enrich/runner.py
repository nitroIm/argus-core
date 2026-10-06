# ============================================================
# ARGUS-Trader — RUNNER (enrich orchestrator)
# ------------------------------------------------------------
# v3: FEATURES_BOOTSTRAP=1 -> run only features.
#     Skips patterns/levels/events/causal/correlate
#     (не нужны для ML, экономим ~60 мин на backfill).
# v2: english logs, no emoji.
# v1: chains features -> patterns -> levels -> events
#     -> causal -> correlate.
# ============================================================

import os
import sys
import time
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.runner")

BOOTSTRAP = (
    os.getenv("FEATURES_BOOTSTRAP", "").strip() == "1"
)

STEPS_ALL = [
    ("features", "enrich.features"),
    ("patterns", "enrich.patterns"),
    ("levels", "enrich.levels"),
    ("events", "enrich.events"),
    ("causal", "enrich.causal"),
    ("correlate", "enrich.correlate"),
]

STEPS_BOOTSTRAP = [
    ("features", "enrich.features"),
]


def run_step(name, module_name):
    log.info("")
    log.info("=" * 60)
    log.info("STEP: %s", name)
    log.info("=" * 60)

    started = time.time()
    try:
        module = __import__(
            module_name, fromlist=["main"],
        )
        module.main()
        elapsed = round(time.time() - started, 1)
        log.info("OK %s in %.1fs", name, elapsed)
        return True, elapsed, None
    except Exception as e:
        elapsed = round(time.time() - started, 1)
        log.error(
            "FAIL %s in %.1fs: %s",
            name, elapsed, e,
        )
        log.error(traceback.format_exc())
        return False, elapsed, str(e)


def main():
    steps = STEPS_BOOTSTRAP if BOOTSTRAP else STEPS_ALL
    log.info("ARGUS-Trader ENRICH RUNNER v3")
    log.info(
        "time: %s",
        datetime.now(timezone.utc).isoformat(),
    )
    log.info(
        "mode: %s",
        "BOOTSTRAP (features only)"
        if BOOTSTRAP else "FULL",
    )
    log.info("steps: %d", len(steps))
    log.info("")

    results = []
    failed = 0

    for name, module_name in steps:
        success, elapsed, error = run_step(
            name, module_name,
        )
        results.append({
            "step": name,
            "success": success,
            "elapsed": elapsed,
            "error": error,
        })
        if not success:
            failed += 1
            log.error(
                "stopping — %s failed", name,
            )
            break

    log.info("")
    log.info("=" * 60)
    log.info("RESULTS")
    log.info("=" * 60)
    for r in results:
        status = "OK" if r["success"] else "FAIL"
        log.info(
            "  %s %-10s %6.1fs",
            status, r["step"], r["elapsed"],
        )
        if r["error"]:
            log.info(
                "     err: %s", r["error"][:100]
            )

    total_time = sum(r["elapsed"] for r in results)
    log.info("")
    log.info("steps total: %d", len(results))
    log.info("ok: %d", len(results) - failed)
    log.info("failed: %d", failed)
    log.info("time: %.1fs", total_time)
    log.info("=" * 60)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()