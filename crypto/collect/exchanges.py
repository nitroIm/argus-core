# ============================================================
# ARGUS-Trader — EXCHANGES
# ------------------------------------------------------------
# Клиенты для OKX, Bitget, Gate, KuCoin, CoinGecko.
# Возвращают нормализованные данные (единый формат) для БД.
# Каждый метод возвращает список dict, готовых к INSERT.
# ------------------------------------------------------------
# v1: начальная версия
# ============================================================

import logging
from datetime import datetime, timezone
from typing import Optional

import requests

# ============================================================
# ОБЩЕЕ
# ============================================================
TIMEOUT = 20
HEADERS = {"User-Agent": "argus-trader/1.0"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.exchanges")


def _ts_ms(ms: int) -> datetime:
    """Milliseconds → datetime UTC."""
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def _ts_s(s: int) -> datetime:
    """Seconds → datetime UTC."""
    return datetime.fromtimestamp(s, tz=timezone.utc)


def _safe_float(v, default=None) -> Optional[float]:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (ValueError, TypeError):
        return default


# ============================================================
# БАЗОВЫЙ КЛИЕНТ
# ============================================================
class BaseClient:
    name = "base"
    base_url = ""

    # ---------- Методы, которые должны переопределить наследники ----------
    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list:
        return []

    def fetch_funding(self, symbol: str, limit: int = 100) -> list:
        return []

    def fetch_oi(self, symbol: str, limit: int = 100) -> list:
        return []

    def fetch_ls(self, symbol: str, limit: int = 100) -> list:
        return []

    def fetch_taker(self, symbol: str, limit: int = 100) -> list:
        return []

    # ---------- Утилиты ----------
    def _get(self, path: str, params: dict = None) -> Optional[dict]:
        url = f"{self.base_url}{path}"
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code != 200:
                log.warning(f"[{self.name}] {path} → HTTP {r.status_code}: {r.text[:120]}")
                return None
            return r.json()
        except Exception as e:
            log.warning(f"[{self.name}] {path} → {e}")
            return None


# ============================================================
# OKX (основной донор)
# ============================================================
class OKXClient(BaseClient):
    name = "okx"
    base_url = "https://www.okx.com"

    def _inst_id(self, symbol: str) -> str:
        return symbol.replace("USDT", "-USDT-SWAP")

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list:
        # OKX bar: 1H, 4H, 1D (не 1h)
        bar_map = {"1m": "1m", "5m": "5m", "15m": "15m",
                   "1h": "1H", "4h": "4H", "1d": "1D"}
        bar = bar_map.get(timeframe)
        if not bar:
            return []

        data = self._get("/api/v5/market/candles", {
            "instId": self._inst_id(symbol), "bar": bar, "limit": min(limit, 300),
        })
        if not data or data.get("code") != "0":
            return []

        rows = []
        for c in data.get("data", []):
            # [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
            rows.append({
                "symbol": symbol, "timeframe": timeframe,
                "timestamp": _ts_ms(int(c[0])),
                "open": _safe_float(c[1]), "high": _safe_float(c[2]),
                "low": _safe_float(c[3]), "close": _safe_float(c[4]),
                "volume": _safe_float(c[5]),
                "source": self.name,
            })
        return rows

    def fetch_funding(self, symbol: str, limit: int = 100) -> list:
        data = self._get("/api/v5/public/funding-rate-history", {
            "instId": self._inst_id(symbol), "limit": min(limit, 100),
        })
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

    def fetch_oi(self, symbol: str, limit: int = 100) -> list:
        ccy = symbol.replace("USDT", "")
        data = self._get("/api/v5/rubik/stat/contracts/open-interest-volume", {
            "ccy": ccy, "period": "1H",
        })
        if not data or data.get("code") != "0":
            return []
        rows = []
        for c in data.get("data", []):
            # [ts, oi, vol]
            rows.append({
                "symbol": symbol,
                "timestamp": _ts_ms(int(c[0])),
                "oi": _safe_float(c[1]),
                "oi_value": None,
                "source": self.name,
            })
        return rows

    def fetch_ls(self, symbol: str, limit: int = 100) -> list:
        ccy = symbol.replace("USDT", "")
        data = self._get("/api/v5/rubik/stat/contracts/long-short-account-ratio", {
            "ccy": ccy, "period": "1H",
        })
        if not data or data.get("code") != "0":
            return []
        rows = []
        for c in data.get("data", []):
            # [ts, ratio]
            ratio = _safe_float(c[1])
            long_pct = None
            short_pct = None
            if ratio is not None and ratio > 0:
                long_pct = round(ratio / (1 + ratio) * 100, 4)
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

    def fetch_taker(self, symbol: str, limit: int = 100) -> list:
        ccy = symbol.replace("USDT", "")
        data = self._get("/api/v5/rubik/stat/taker-volume", {
            "ccy": ccy, "instType": "CONTRACTS",
        })
        if not data or data.get("code") != "0":
            return []
        rows = []
        for c in data.get("data", []):
            # [ts, sellVol, buyVol]
            rows.append({
                "symbol": symbol,
                "timestamp": _ts_ms(int(c[0])),
                "buy_vol": _safe_float(c[2]),
                "sell_vol": _safe_float(c[1]),
                "source": self.name,
            })
        return rows


# ============================================================
# BITGET
# ============================================================
class BitgetClient(BaseClient):
    name = "bitget"
    base_url = "https://api.bitget.com"

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list:
        gran_map = {"1m": "1m", "5m": "5m", "15m": "15m",
                    "1h": "1H", "4h": "4H", "1d": "1D"}
        gran = gran_map.get(timeframe)
        if not gran:
            return []
        data = self._get("/api/v2/mix/market/candles", {
            "symbol": symbol, "granularity": gran,
            "limit": min(limit, 200), "productType": "USDT-FUTURES",
        })
        if not data or data.get("code") != "00000":
            return []
        rows = []
        for c in data.get("data", []):
            # [ts, o, h, l, c, baseVol, quoteVol]
            rows.append({
                "symbol": symbol, "timeframe": timeframe,
                "timestamp": _ts_ms(int(c[0])),
                "open": _safe_float(c[1]), "high": _safe_float(c[2]),
                "low": _safe_float(c[3]), "close": _safe_float(c[4]),
                "volume": _safe_float(c[5]),
                "source": self.name,
            })
        return rows

    def fetch_funding(self, symbol: str, limit: int = 100) -> list:
        data = self._get("/api/v2/mix/market/history-fund-rate", {
            "symbol": symbol, "productType": "USDT-FUTURES",
            "pageSize": min(limit, 100),
        })
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

    def fetch_ls(self, symbol: str, limit: int = 100) -> list:
        data = self._get("/api/v2/mix/market/account-long-short", {
            "symbol": symbol, "productType": "USDT-FUTURES", "period": "1H",
        })
        if not data or data.get("code") != "00000":
            return []
        rows = []
        for c in data.get("data", []):
            rows.append({
                "symbol": symbol,
                "timestamp": _ts_ms(int(c["ts"])),
                "ls_ratio": _safe_float(c.get("longShortRatio")),
                "long_pct": _safe_float(c.get("longAccountRatio")),
                "short_pct": _safe_float(c.get("shortAccountRatio")),
                "source": self.name,
            })
        return rows


# ============================================================
# GATE
# ============================================================
class GateClient(BaseClient):
    name = "gate"
    base_url = "https://api.gateio.ws"

    def _contract(self, symbol: str) -> str:
        return symbol.replace("USDT", "_USDT")

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list:
        interval_map = {"1m": "1m", "5m": "5m", "15m": "15m",
                        "1h": "1h", "4h": "4h", "1d": "1d"}
        interval = interval_map.get(timeframe)
        if not interval:
            return []
        data = self._get("/api/v4/futures/usdt/candlesticks", {
            "contract": self._contract(symbol),
            "interval": interval, "limit": min(limit, 100),
        })
        if not isinstance(data, list):
            return []
        rows = []
        for c in data:
            rows.append({
                "symbol": symbol, "timeframe": timeframe,
                "timestamp": _ts_s(int(c["t"])),
                "open": _safe_float(c["o"]), "high": _safe_float(c["h"]),
                "low": _safe_float(c["l"]), "close": _safe_float(c["c"]),
                "volume": _safe_float(c.get("v")),
                "source": self.name,
            })
        return rows

    def fetch_funding(self, symbol: str, limit: int = 100) -> list:
        data = self._get("/api/v4/futures/usdt/funding_rate", {
            "contract": self._contract(symbol), "limit": min(limit, 100),
        })
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

    def fetch_oi(self, symbol: str, limit: int = 100) -> list:
        # Gate отдаёт OI вместе с LS в contract_stats
        data = self._get("/api/v4/futures/usdt/contract_stats", {
            "contract": self._contract(symbol), "interval": "1h", "limit": min(limit, 100),
        })
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
                "oi_value": _safe_float(c.get("open_interest_usd")),
                "source": self.name,
            })
        return rows

    def fetch_ls(self, symbol: str, limit: int = 100) -> list:
        data = self._get("/api/v4/futures/usdt/contract_stats", {
            "contract": self._contract(symbol), "interval": "1h", "limit": min(limit, 100),
        })
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
                long_pct = round(lsr / (1 + lsr) * 100, 4)
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


# ============================================================
# KUCOIN
# ============================================================
class KuCoinClient(BaseClient):
    name = "kucoin"
    base_url = "https://api-futures.kucoin.com"

    def _symbol(self, symbol: str) -> str:
        # BTCUSDT → XBTUSDTM
        return "XBT" + symbol[3:] + "M" if symbol.startswith("BTC") else symbol + "M"

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list:
        gran_map = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
        gran = gran_map.get(timeframe)
        if not gran:
            return []
        data = self._get("/api/v1/kline/query", {
            "symbol": self._symbol(symbol), "granularity": gran,
        })
        if not data or data.get("code") != "200000":
            return []
        rows = []
        for c in data.get("data", []):
            # [ts, o, h, l, c, vol]
            rows.append({
                "symbol": symbol, "timeframe": timeframe,
                "timestamp": _ts_ms(int(c[0])),
                "open": _safe_float(c[1]), "high": _safe_float(c[2]),
                "low": _safe_float(c[3]), "close": _safe_float(c[4]),
                "volume": _safe_float(c[5]),
                "source": self.name,
            })
        return rows

    def fetch_funding(self, symbol: str, limit: int = 100) -> list:
        import time as _t
        to_ms = int(_t.time() * 1000)
        from_ms = to_ms - (limit * 8 * 3600 * 1000)  # 8ч интервалы
        data = self._get("/api/v1/contract/funding-rates", {
            "symbol": self._symbol(symbol), "from": from_ms, "to": to_ms,
        })
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


# ============================================================
# COINGECKO (контекст + сверка)
# ============================================================
class CoinGeckoClient(BaseClient):
    name = "coingecko"
    base_url = "https://api.coingecko.com"

    def fetch_context(self) -> list:
        """Возвращает одну запись с метриками рынка."""
        # Простой запрос — цены + mcap
        simple = self._get("/api/v3/simple/price", {
            "ids": "bitcoin,ethereum",
            "vs_currencies": "usd",
            "include_market_cap": "true",
            "include_24hr_vol": "true",
        })
        # Глобальный запрос — доминация, общий mcap
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


# ============================================================
# РЕЕСТР
# ============================================================
CLIENTS = {
    "okx": OKXClient(),
    "bitget": BitgetClient(),
    "gate": GateClient(),
    "kucoin": KuCoinClient(),
    "coingecko": CoinGeckoClient(),
}


# ============================================================
# ТЕСТ
# ============================================================
if __name__ == "__main__":
    print("📡 ARGUS-Trader — тест клиентов бирж")
    print("=" * 50)

    # Тест OHLCV
    for name in ["okx", "bitget", "gate", "kucoin"]:
        client = CLIENTS[name]
        rows = client.fetch_ohlcv("BTCUSDT", "1h", limit=3)
        print(f"\n{name.upper()} OHLCV: {len(rows)} записей")
        for r in rows[:1]:
            print(f"   {r['timestamp']} O={r['open']} C={r['close']} V={r['volume']}")

    # Тест funding
    print()
    for name in ["okx", "bitget", "gate"]:
        client = CLIENTS[name]
        rows = client.fetch_funding("BTCUSDT", limit=3)
        print(f"{name.upper()} funding: {len(rows)} записей")
        for r in rows[:1]:
            print(f"   {r['timestamp']} rate={r['rate']}")

    # Тест CoinGecko
    print()
    cg = CLIENTS["coingecko"]
    ctx = cg.fetch_context()
    print(f"COINGECKO context: {len(ctx)} записей")
    for r in ctx:
        print(f"   BTC=${r['btc_price_usd']}, dominance={r['btc_dominance']}%")

    print("\n" + "=" * 50)