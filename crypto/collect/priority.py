# ============================================================
# ARGUS-Trader - PRIORITY
# ------------------------------------------------------------
# v3: removed methods not present in exchanges.py:
#     - bitget.fetch_oi (no method)
#     - gate.fetch_taker (no method)
#     These caused silent skip + wasted attempts.
# v2: fix imports (sys.path for launch from any dir).
# v1: initial.
# ============================================================

import sys
import logging
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))

log = logging.getLogger("crypto.priority")


PRIORITY = {
    "ohlcv": [
        ("okx",    "fetch_ohlcv"),
        ("bitget", "fetch_ohlcv"),
        ("gate",   "fetch_ohlcv"),
        ("kucoin", "fetch_ohlcv"),
    ],
    "funding": [
        ("okx",    "fetch_funding"),
        ("bitget", "fetch_funding"),
        ("gate",   "fetch_funding"),
        ("kucoin", "fetch_funding"),
    ],
    "oi": [
        ("okx",  "fetch_oi"),
        ("gate", "fetch_oi"),
    ],
    "ls_ratio": [
        ("okx",    "fetch_ls"),
        ("bitget", "fetch_ls"),
        ("gate",   "fetch_ls"),
    ],
    "taker": [
        ("okx", "fetch_taker"),
    ],
}


def get_priority(metric: str) -> list:
    return PRIORITY.get(metric, [])


def supported_metrics() -> list:
    return list(PRIORITY.keys())


def get_metrics_table() -> str:
    lines = ["PRIORITY TABLE", "=" * 60]
    for metric, sources in PRIORITY.items():
        chain = " -> ".join(
            name for name, _ in sources
        )
        lines.append(
            "  %-12s : %s" % (metric, chain)
        )
    lines.append("=" * 60)
    return "\n".join(lines)


if __name__ == "__main__":
    print(get_metrics_table())