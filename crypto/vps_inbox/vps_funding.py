# ============================================================
# ARGUS - VPS FUNDING BOOTSTRAP
# ------------------------------------------------------------
# Разовый сбор funding_rates с Binance Futures.
# Запускается с VPS (GitHub Actions блокирует Binance).
# Кладёт CSV в crypto/vps_inbox/funding/.
# В БД НЕ пишет — это делает отдельный workflow.
#
# v1: первая версия.
# ============================================================

import os
import io
import csv
import base64
import logging
import requests
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("vps.funding")

BINANCE_BASE = "https://fapi.binance.com"
FUNDING_PATH = "/fapi/v1/fundingRate"

SYMBOLS_DEFAULT = [
    "BTCUSDT", "ETHUSDT",
    "SOLUSDT", "BNBUSDT",
]

DAYS_DEFAULT = 400
INBOX_DIR = "crypto/vps_inbox/funding"
TIMEOUT = 30


def fetch_funding(symbol, days=DAYS_DEFAULT):
    end_ms = int(
        datetime.now(timezone.utc).timestamp() * 1000
    )
    start_ms = end_ms - days * 86400 * 1000

    url = BINANCE_BASE + FUNDING_PATH
    params = {
        "symbol": symbol,
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": 1000,
    }
    try:
        r = requests.get(
            url, params=params, timeout=TIMEOUT,
        )
    except Exception as e:
        log.error("%s: request: %s", symbol, e)
        return None

    if r.status_code == 451:
        log.error("%s: HTTP 451 region", symbol)
        return None
    if r.status_code == 429:
        log.error("%s: HTTP 429 rate", symbol)
        return None
    if r.status_code != 200:
        log.error(
            "%s: HTTP %d %s",
            symbol, r.status_code, r.text[:120],
        )
        return None

    try:
        data = r.json()
    except Exception as e:
        log.error("%s: json: %s", symbol, e)
        return None

    if not isinstance(data, list):
        log.error("%s: not list", symbol)
        return None

    rows = []
    for item in data:
        try:
            ts_ms = int(item["fundingTime"])
            rate = item["fundingRate"]
            ts = datetime.fromtimestamp(
                ts_ms / 1000.0, tz=timezone.utc,
            )
            rows.append({
                "timestamp": ts.isoformat(),
                "rate": rate,
            })
        except Exception as e:
            log.warning(
                "%s: bad row: %s", symbol, e,
            )
    return rows


def verify_funding(symbol, rows):
    out = {
        "symbol": symbol,
        "rows": 0,
        "min_ts": None,
        "max_ts": None,
        "ok": False,
        "errors": [],
    }
    if not rows:
        out["errors"].append("empty")
        return out
    out["rows"] = len(rows)

    ts_all = []
    for r in rows:
        try:
            ts = datetime.fromisoformat(
                r["timestamp"]
            )
        except Exception:
            out["errors"].append("bad ts")
            continue
        if r["rate"] is None or r["rate"] == "":
            out["errors"].append("null rate")
            continue
        ts_all.append(ts)

    if not ts_all:
        out["errors"].append("no valid ts")
        return out

    ts_all.sort()
    out["min_ts"] = ts_all[0].isoformat()
    out["max_ts"] = ts_all[-1].isoformat()

    if len(set(ts_all)) != len(ts_all):
        out["errors"].append("duplicate ts")

    if len(rows) < 100:
        out["errors"].append(
            "too few: " + str(len(rows))
        )

    now = datetime.now(timezone.utc)
    age_h = (
        now - ts_all[-1]
    ).total_seconds() / 3600
    if age_h > 48:
        out["errors"].append(
            "stale " + str(int(age_h)) + "h"
        )

    if not out["errors"]:
        out["ok"] = True
    return out


def rows_to_csv(rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["timestamp", "rate"])
    for r in rows:
        w.writerow([r["timestamp"], r["rate"]])
    return buf.getvalue()


def gh_put_file(repo, path, content_str,
                pat, branch="main"):
    if not repo or not pat:
        return False, "no repo/pat"

    url = "https://api.github.com/repos/"
    url += repo
    url += "/contents/"
    url += path

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + pat,
        "X-GitHub-Api-Version": "2022-11-28",
    }

    sha = None
    try:
        g = requests.get(
            url, headers=headers, timeout=15,
        )
        if g.status_code == 200:
            sha = g.json().get("sha")
    except Exception:
        pass

    content_b64 = base64.b64encode(
        content_str.encode("utf-8")
    ).decode("ascii")

    fname = path.split("/")[-1]
    data = {
        "message": "vps: " + fname,
        "content": content_b64,
        "branch": branch,
    }
    if sha:
        data["sha"] = sha

    try:
        r = requests.put(
            url, headers=headers,
            json=data, timeout=20,
        )
    except Exception as e:
        return False, str(e)

    if r.status_code in (200, 201):
        return True, "OK"
    if r.status_code == 401:
        return False, "401 bad PAT"
    if r.status_code == 403:
        return False, "403 no scope"
    if r.status_code == 409:
        return False, "409 conflict"
    if r.status_code == 422:
        return False, "422 invalid"
    return False, "HTTP " + str(r.status_code)


def bootstrap(symbols=None, days=DAYS_DEFAULT,
              repo=None, pat=None, notify=None):
    if symbols is None:
        symbols = SYMBOLS_DEFAULT
    if repo is None:
        repo = os.getenv("GITHUB_REPO") or ""
    if pat is None:
        pat = (
            os.getenv("GH_PAT")
            or os.getenv("GITHUB_PAT")
            or ""
        )

    today = datetime.now(timezone.utc).strftime(
        "%Y-%m-%d"
    )
    results = {}

    for sym in symbols:
        if notify:
            notify("🪙 " + sym + ": fetch...")
        rows = fetch_funding(sym, days=days)
        v = verify_funding(sym, rows or [])
        results[sym] = {
            "rows": v["rows"],
            "ok": v["ok"],
            "errors": v["errors"],
            "github": "skip",
        }
        if not v["ok"]:
            log.warning(
                "%s verify: %s", sym, v["errors"],
            )
            continue
        csv_str = rows_to_csv(rows)
        fname = sym + "_" + today + ".csv"
        gpath = INBOX_DIR + "/" + fname
        ok, msg = gh_put_file(
            repo, gpath, csv_str, pat,
        )
        results[sym]["github"] = (
            "OK" if ok else msg
        )
        log.info(
            "%s: %d rows gh=%s",
            sym, v["rows"], results[sym]["github"],
        )

    return results


def summary_text(results):
    lines = ["🪙 Funding bootstrap", ""]
    for sym, r in results.items():
        line = sym + ": " + str(r["rows"]) + " rows"
        if not r["ok"]:
            line += " ✗ " + "; ".join(r["errors"])
        else:
            line += " ✓ gh=" + str(r["github"])
        lines.append(line)
    return "\n".join(lines)