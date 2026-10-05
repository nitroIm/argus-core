# ============================================================
# ARGUS-Trader — EXCHANGES
# ------------------------------------------------------------
# v2: + fetch_ohlcv_range (history pagination) for bootstrap.
#     OKX, Bitget, Gate support historical bars.
# v1: начальная версия.
# ============================================================

import logging
import time as _time
from datetime import datetime, timezone
from typing import Optional

import requests

TIMEOUT = 20
HEADERS = {"User-Agent": "argus-trader/1.0"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.exchanges")


def _ts_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def _ts_s(s: int) -> datetime:
    return datetime.fromtimestamp(s, tz=timezone.utc)


def _safe_float(v, default=None) -> Optional[float]:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (ValueError, TypeError):
        return default


class BaseClient:
    name = "base"
    base_url = ""

    def fetch_ohlcv(self, symbol, timeframe, limit=100):
        return []

    def fetch_funding(self, symbol, limit=100):
        return []

    def fetch_oi(self, symbol, limit=100):
        return []

    def fetch_ls(self, symbol, limit=100):
        return []

    def fetch_taker(self, symbol, limit=100):
        return []

    def fetch_ohlcv_range(self, symbol, timeframe,
                          start_ms, end_ms, max_bars=3000):
        """Historical bars between start_ms and end_ms.
        Override in subclasses."""
        return []

    def _get(self, path, params=None):
        url = f"{self.base_url}{path}"
        try:
            r = requests.get(
                url, params=params,
                headers=HEADERS, timeout=TIMEOUT,
            )
            if r.status_code != 200:
                log.warning(
                    "[%s] %s → HTTP %d: %s",
                    self.name, path, r.status_code,
                    r.text[:120],
                )
                return None
            return r.json()
        except Exception as e:
            log.warning("[%s] %s → %s", self.name, path, e)
            return None


class OKXClient(BaseClient):
    name = "okx"
    base_url = "https://www.okx.com"

    def _inst_id(self, symbol):
        return symbol.replace("USDT", "-USDT-SWAP")

    def fetch_ohlcv(self, symbol, timeframe, limit=100):
        bar_map = {
            "1m": "1m", "5m": "5m", "15m": "15m",
            "1h": "1H", "4h": "4H", "1d": "1D",
        }
        bar = bar_map.get(timeframe)
        if not bar:
            return []
        data = self._get("/api/v5/market/candles", {
            "instId": self._inst_id(symbol),
            "bar": bar, "limit": min(limit, 300),
        })
        if not data or data.get("code") != "0":
            return []
        rows = []
        for c in data.get("data", []):
            rows.append({
                "symbol": symbol, "timeframe": timeframe,
                "timestamp": _ts_ms(int(c[0])),
                "open": _safe_float(c[1]),
                "high": _safe_float(c[2]),
                "low": _safe_float(c[3]),
                "close": _safe_float(c[4]),
                "volume": _safe_float(c[5]),
                "source": self.name,
            })
        return rows

    def fetch_ohlcv_range(self, symbol, timeframe,
                          start_ms, end_ms, max_bars=3000):
        bar_map = {
            "1m": "1m", "5m": "5m", "15m": "15m",
            "1h": "1H", "4h": "4H", "1d": "1D",
        }
        bar = bar_map.get(timeframe)
        if not bar:
            return []

        out = []
        cursor = end_ms
        attempts = 0
        max_attempts = 60

        while len(out) < max_bars and cursor > start_ms:
            attempts += 1
            if attempts > max_attempts:
                break

            data = self._get(
                "/api/v5/market/history-candles",
                {
                    "instId": self._inst_id(symbol),
                    "bar": bar,
                    "after": cursor,
                    "limit": 100,
                },
            )
            if not data or data.get("code") != "0":
                break
            rows = data.get("data", [])
            if not rows:
                break

            added = 0
            oldest = cursor
            for c in rows:
                ts = int(c[0])
                if ts < start_ms:
                    continue
                if ts >= end_ms:
                    continue
                out.append({
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "timestamp": _ts_ms(ts),
                    "open": _safe_float(c[1]),
                    "high": _safe_float(c[2]),
                    "low": _safe_float(c[3]),
                    "close": _safe_float(c[4]),
                    "volume": _safe_float(c[5]),
                    "source": self.name,
                })
                added += 1
                if ts < oldest:
                    oldest = ts

            if added == 0:
                break
            if oldest >= cursor:
                break
            cursor = oldest
            _time.sleep(0.3)

        return out

    def fetch_funding(self, symbol, limit=100):
        data = self._get(
            "/api/v5/public/funding-rate-history",
            {
                "instId": self._inst_id(symbol),
                "limit": min(limit, 100),
            },
        )
        if not data or data.get("code") != "0":
            return []
        rows = []
        for c in data.get("data", []):
            rows.append({
                "symbol": symbol,
                "timestamp": _ts_ms(int(c["fundingTime"])),
                "rate": _safe_float(c["fundingRate"]),
                "source": self.name,
            })
        return rows

    def fetch_oi(self, symbol, limit=100):
        ccy = symbol.replace("USDT", "")
        data = self._get(
            "/api/v5/rubik/stat/contracts/open-interest-volume",
            {"ccy": ccy, "period": "1H"},
        )
        if not data or data.get("code") != "0":
            return []
        rows = []
        for c in data.get("data", []):
            rows.append({
                "symbol": symbol,
                "timestamp": _ts_ms(int(c[0])),
                "oi": _safe_float(c[1]),
                "oi_value": None,
                "source": self.name,
            })
        return rows

    def fetch_ls(self, symbol, limit=100):
        ccy = symbol.replace("USDT", "")
        data = self._get(
            "/api/v5/rubik/stat/contracts/"
            "long-short-account-ratio",
            {"ccy": ccy, "period": "1H"},
        )
        if not data or data.get("code") != "0":
            return []
        rows = []
        for c in data.get("data", []):
            ratio = _safe_float(c[1])
            long_pct = None
            short_pct = None
            if ratio is not None and ratio > 0:
                long_pct = round(
                    ratio / (1 + ratio) * 100, 4,
                )
                short_pct = round(100 - long_pct, 4)
            rows.append({
                "symbol": symbol,
                "timestamp": _ts_ms(int(c[0])),
                "ls_ratio": ratio,
                "long_pct": long_pct,
                "short_pct": short_pct,
                "source": self.name,
            })
        return rows

    def fetch_taker(self, symbol, limit=100):
        ccy = symbol.replace("USDT", "")
        data = self._get(
            "/api/v5/rubik/stat/taker-volume",
            {"ccy": ccy, "instType": "CONTRACTS"},
        )
        if not data or data.get("code") != "0":
            return []
        rows = []
        for c in data.get("data", []):
            rows.append({
                "symbol": symbol,
                "timestamp": _ts_ms(int(c[0])),
                "buy_vol": _safe_float(c[2]),
                "sell_vol": _safe_float(c[1]),
                "source": self.name,
            })
        return rows


class BitgetClient(BaseClient):
    name = "bitget"
    base_url = "https://api.bitget.com"

    def fetch_ohlcv(self, symbol, timeframe, limit=100):
        gran_map = {
            "1m": "1m", "5m": "5m", "15m": "15m",
            "1h": "1H", "4h": "4H", "1d": "1D",
        }
        gran = gran_map.get(timeframe)
        if not gran:
            return []
        data = self._get("/api/v2/mix/market/candles", {
            "symbol": symbol,
            "granularity": gran,
            "limit": min(limit, 200),
            "productType": "USDT-FUTURES",
        })
        if not data or data.get("code") != "00000":
            return []
        rows = []
        for c in data.get("data", []):
            rows.append({
                "symbol": symbol, "timeframe": timeframe,
                "timestamp": _ts_ms(int(c[0])),
                "open": _safe_float(c[1]),
                "high": _safe_float(c[2]),
                "low": _safe_float(c[3]),
                "close": _safe_float(c[4]),
                "volume": _safe_float(c[5]),
                "source": self.name,
            })
        return rows

    def fetch_ohlcv_range(self, symbol, timeframe,
                          start_ms, end_ms, max_bars=3000):
        gran_map = {
            "1m": "1m", "5m": "5m", "15m": "15m",
            "1h": "1H", "4h": "4H", "1d": "1D",
        }
        gran = gran_map.get(timeframe)
        if not gran:
            return []

        out = []
        cursor = end_ms
        attempts = 0
        max_attempts = 60

        while len(out) < max_bars and cursor > start_ms:
            attempts += 1
            if attempts > max_attempts:
                break

            data = self._get(
                "/api/v2/mix/market/history-candles",
                {
                    "symbol": symbol,
                    "granularity": gran,
                    "endTime": cursor,
                    "limit": 200,
                    "productType": "USDT-FUTURES",
                },
            )
            if not data or data.get("code") != "00000":
                break
            rows = data.get("data", [])
            if not rows:
                break

            added = 0
            oldest = cursor
            for c in rows:
                ts = int(c[0])
                if ts < start_ms:
                    continue
                if ts >= end_ms:
                    continue
                out.append({
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "timestamp": _ts_ms(ts),
                    "open": _safe_float(c[1]),
                    "high": _safe_float(c[2]),
                    "low": _safe_float(c[3]),
                    "close": _safe_float(c[4]),
                    "volume": _safe_float(c[5]),
                    "source": self.name,
                })
                added += 1
                if ts < oldest:
                    oldest = ts

            if added == 0:
                break
            if oldest >= cursor:
                break
            cursor = oldest
            _time.sleep(0.3)

        return out

    def fetch_funding(self, symbol, limit=100):
        data = self._get(
            "/api/v2/mix/market/history-fund-rate",
            {
                "symbol": symbol,
                "productType": "USDT-FUTURES",
                "pageSize": min(limit, 100),
            },
        )
        if not data or data.get("code") != "00000":
            return []
        rows = []
        for c in data.get("data", []):
            rows.append({
                "symbol": symbol,
                "timestamp": _ts_ms(int(c["fundingTime"])),
                "rate": _safe_float(c["fundingRate"]),
                "source": self.name,
            })
        return rows

    def fetch_ls(self, symbol, limit=100):
        data = self._get(
            "/api/v2/mix/market/account-long-short",
            {
                "symbol": symbol,
                "productType": "USDT-FUTURES",
                "period": "1H",
            },
        )
        if not data or data.get("code") != "00000":
            return []
        rows = []
        for c in data.get("data", []):
            rows.append({
                "symbol": symbol,
                "timestamp": _ts_ms(int(c["ts"])),
                "ls_ratio": _safe_float(
                    c.get("longShortRatio")
                ),
                "long_pct": _safe_float(
                    c.get("longAccountRatio")
                ),
                "short_pct": _safe_float(
                    c.get("shortAccountRatio")
                ),
                "source": self.name,
            })
        return rows


class GateClient(BaseClient):
    name = "gate"
    base_url = "https://api.gateio.ws"

    def _contract(self, symbol):
        return symbol.replace("USDT", "_USDT")

    def fetch_ohlcv(self, symbol, timeframe, limit=100):
        interval_map = {
            "1m": "1m", "5m": "5m", "15m": "15m",
            "1h": "1h", "4h": "4h", "1d": "1d",
        }
        interval = interval_map.get(timeframe)
        if not interval:
            return []
        data = self._get(
            "/api/v4/futures/usdt/candlesticks",
            {
                "contract": self._contract(symbol),
                "interval": interval,
                "limit": min(limit, 100),
            },
        )
        if not isinstance(data, list):
            return []
        rows = []
        for c in data:
            rows.append({
                "symbol": symbol, "timeframe": timeframe,
                "timestamp": _ts_s(int(c["t"])),
                "open": _safe_float(c["o"]),
                "high": _safe_float(c["h"]),
                "low": _safe_float(c["l"]),
                "close": _safe_float(c["c"]),
                "volume": _safe_float(c.get("v")),
                "source": self.name,
            })
        return rows

    def fetch_ohlcv_range(self, symbol, timeframe,
                          start_ms, end_ms, max_bars=3000):
        interval_map = {
            "1m": "1m", "5m": "5m", "15m": "15m",
            "1h": "1h", "4h": "4h", "1d": "1d",
        }
        interval = interval_map.get(timeframe)
        if not interval:
            return []

        out = []
        to_s = end_ms // 1000
        start_s = start_ms // 1000
        attempts = 0
        max_attempts = 30

        while len(out) < max_bars and to_s > start_s:
            attempts += 1
            if attempts > max_attempts:
                break

            data = self._get(
                "/api/v4/futures/usdt/candlesticks",
                {
                    "contract": self._contract(symbol),
                    "interval": interval,
                    "from": start_s,
                    "to": to_s,
                    "limit": 1000,
                },
            )
            if not isinstance(data, list) or not data:
                break

            added = 0
            for c in data:
                ts_s = int(c["t"])
                if ts_s < start_s:
                    continue
                if ts_s >= to_s:
                    continue
                out.append({
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "timestamp": _ts_s(ts_s),
                    "open": _safe_float(c["o"]),
                    "high": _safe_float(c["h"]),
                    "low": _safe_float(c["l"]),
                    "close": _safe_float(c["c"]),
                    "volume": _safe_float(c.get("v")),
                    "source": self.name,
                })
                added += 1

            if added == 0:
                break

            # Gate отдаёт от старых к новым, сдвигаем to назад
            first_ts = int(data[0]["t"])
            if first_ts >= to_s:
                break
            to_s = first_ts
            _time.sleep(0.3)

        return out

    def fetch_funding(self, symbol, limit=100):
        data = self._get(
            "/api/v4/futures/usdt/funding_rate",
            {
                "contract": self._contract(symbol),
                "limit": min(limit, 100),
            },
        )
        if not isinstance(data, list):
            return []
        rows = []
        for c in data:
            rows.append({
                "symbol": symbol,
                "timestamp": _ts_s(int(c["t"])),
                "rate": _safe_float(c.get("r")),
                "source": self.name,
            })
        return rows

    def fetch_oi(self, symbol, limit=100):
        data = self._get(
            "/api/v4/futures/usdt/contract_stats",
            {
                "contract": self._contract(symbol),
                "interval": "1h",
                "limit": min(limit, 100),
            },
        )
        if not isinstance(data, list):
            return []
        rows = []
        for c in data:
            ts = c.get("time")
            if ts is None:
                continue
            rows.append({
                "symbol": symbol,
                "timestamp": _ts_s(int(ts)),
                "oi": _safe_float(c.get("open_interest")),
                "oi_value": _safe_float(
                    c.get("open_interest_usd")
                ),
                "source": self.name,
            })
        return rows

    def fetch_ls(self, symbol, limit=100):
        data = self._get(
            "/api/v4/futures/usdt/contract_stats",
            {
                "contract": self._contract(symbol),
                "interval": "1h",
                "limit": min(limit, 100),
            },
        )
        if not isinstance(data, list):
            return []
        rows = []
        for c in data:
            ts = c.get("time")
            if ts is None:
                continue
            lsr = _safe_float(c.get("lsr_account"))
            long_pct = None
            short_pct = None
            if lsr is not None and lsr > 0:
                long_pct = round(
                    lsr / (1 + lsr) * 100, 4,
                )
                short_pct = round(100 - long_pct, 4)
            rows.append({
                "symbol": symbol,
                "timestamp": _ts_s(int(ts)),
                "ls_ratio": lsr,
                "long_pct": long_pct,
                "short_pct": short_pct,
                "source": self.name,
            })
        return rows


class KuCoinClient(BaseClient):
    name = "kucoin"
    base_url = "https://api-futures.kucoin.com"

    def _symbol(self, symbol):
        if symbol.startswith("BTC"):
            return "XBT" + symbol[3:] + "M"
        return symbol + "M"

    def fetch_ohlcv(self, symbol, timeframe, limit=100):
        gran_map = {
            "1m": 1, "5m": 5, "15m": 15,
            "1h": 60, "4h": 240, "1d": 1440,
        }
        gran = gran_map.get(timeframe)
        if not gran:
            return []
        data = self._get("/api/v1/kline/query", {
            "symbol": self._symbol(symbol),
            "granularity": gran,
        })
        if not data or data.get("code") != "200000":
            return []
        rows = []
        for c in data.get("data", []):
            rows.append({
                "symbol": symbol, "timeframe": timeframe,
                "timestamp": _ts_ms(int(c[0])),
                "open": _safe_float(c[1]),
                "high": _safe_float(c[2]),
                "low": _safe_float(c[3]),
                "close": _safe_float(c[4]),
                "volume": _safe_float(c[5]),
                "source": self.name,
            })
        return rows

    def fetch_funding(self, symbol, limit=100):
        to_ms = int(_time.time() * 1000)
        from_ms = to_ms - (limit * 8 * 3600 * 1000)
        data = self._get(
            "/api/v1/contract/funding-rates",
            {
                "symbol": self._symbol(symbol),
                "from": from_ms,
                "to": to_ms,
            },
        )
        if not data or data.get("code") != "200000":
            return []
        rows = []
        for c in data.get("data", []):
            rows.append({
                "symbol": symbol,
                "timestamp": _ts_ms(int(c["timepoint"])),
                "rate": _safe_float(c.get("fundingRate")),
                "source": self.name,
            })
        return rows


class CoinGeckoClient(BaseClient):
    name = "coingecko"
    base_url = "https://api.coingecko.com"

    def fetch_context(self):
        simple = self._get("/api/v3/simple/price", {
            "ids": "bitcoin,ethereum",
            "vs_currencies": "usd",
            "include_market_cap": "true",
            "include_24hr_vol": "true",
        })
        glob = self._get("/api/v3/global")
        if not simple or not glob:
            return []
        btc = simple.get("bitcoin", {})
        eth = simple.get("ethereum", {})
        g = glob.get("data", {})
        dom = g.get("market_cap_percentage", {}).get("btc")
        total_mcap = g.get("total_market_cap", {}).get("usd")
        total_vol = g.get("total_volume", {}).get("usd")
        return [{
            "timestamp": datetime.now(timezone.utc),
            "btc_mcap": _safe_float(btc.get("usd_market_cap")),
            "eth_mcap": _safe_float(eth.get("usd_market_cap")),
            "btc_dominance": _safe_float(dom),
            "total_mcap": _safe_float(total_mcap),
            "total_volume_24h": _safe_float(total_vol),
            "btc_price_usd": _safe_float(btc.get("usd")),
            "eth_price_usd": _safe_float(eth.get("usd")),
            "source": self.name,
        }]


CLIENTS = {
    "okx": OKXClient(),
    "bitget": BitgetClient(),
    "gate": GateClient(),
    "kucoin": KuCoinClient(),
    "coingecko": CoinGeckoClient(),
}