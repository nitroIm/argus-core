# ============================================================
# ARGUS - MEXC ACCOUNT READER v2 [PRODUCTION]
# ------------------------------------------------------------
# v2: USDC по курсу, обработка ошибок.
# Spot баланс.
# ------------------------------------------------------------
# Требования:
#   pip install requests
# ============================================================

import sys
import logging
from pathlib import Path
from datetime import datetime, timezone

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from client import MexcClient
from client import is_configured

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("mexc.account")


def fmt_usd(p):
    if p is None:
        return "?"
    if p >= 1000:
        return "$" + format(p, ",.2f")
    if p >= 1:
        return "$" + format(p, ".2f")
    return "$" + format(p, ".4f")


def get_spot_balance(client):
    data = client.signed_get("/api/v3/account")
    if not data:
        return []

    out = []
    for b in data.get("balances", []):
        try:
            free = float(b.get("free", 0))
            locked = float(b.get("locked", 0))
        except Exception:
            continue
        total = free + locked
        if total > 0:
            out.append({
                "asset": b.get("asset", "?"),
                "free": free,
                "locked": locked,
                "total": total,
            })
    return out


def get_ticker_price(client, symbol):
    data = client.public_get(
        "/api/v3/ticker/price",
        {"symbol": symbol},
    )
    if data and "price" in data:
        try:
            return float(data["price"])
        except Exception:
            return None
    return None


def get_account_summary(client):
    summary = {
        "checked_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "spot": [],
        "total_usdt": 0.0,
    }

    balances = get_spot_balance(client)
    for b in balances:
        asset = b["asset"]
        total = b["total"]

        if asset in ("USDT", "USDC"):
            usd_value = total
        else:
            symbol = asset + "USDT"
            price = get_ticker_price(
                client, symbol,
            )
            usd_value = (
                total * price
                if price else 0
            )

        b["usd_value"] = round(usd_value, 4)
        summary["spot"].append(b)
        summary["total_usdt"] += usd_value

    summary["total_usdt"] = round(
        summary["total_usdt"], 2,
    )
    return summary


def fmt_summary(summary):
    lines = []
    lines.append("💼 MEXC аккаунт")
    lines.append(
        "Всего: " + fmt_usd(summary["total_usdt"])
    )
    lines.append("")

    shown = False
    for b in summary["spot"]:
        if b["usd_value"] < 0.01:
            continue
        line = "  " + b["asset"] + ": "
        line += str(round(b["total"], 6))
        if b["asset"] not in ("USDT", "USDC"):
            line += " (" + fmt_usd(
                b["usd_value"]
            ) + ")"
        lines.append(line)
        shown = True

    if not shown:
        lines.append("  (нет активов)")
    return "\n".join(lines)


def main():
    log.info("=" * 50)
    log.info("MEXC account reader v2")
    log.info("=" * 50)

    if not is_configured():
        log.error("MEXC_API_KEY / SECRET not set")
        sys.exit(1)

    client = MexcClient()
    summary = get_account_summary(client)

    log.info("")
    log.info(fmt_summary(summary))
    log.info("")

    if summary["total_usdt"] > 0:
        log.info("OK")
    else:
        log.warning("Empty balance or no access")


if __name__ == "__main__":
    main()