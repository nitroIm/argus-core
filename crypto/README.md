# 🪙 ARGUS-TRADER — GUIDE v4

Обновлено: 2026-09-22
Статус: v4 (отчёты + графики +
аудио + пульт)

━━━━━━━━━━━━━━━━━━━━
1. МИССИЯ
━━━━━━━━━━━━━━━━━━━━

Сбор качественных рыночных данных
BTC/ETH → поиск закономерностей
→ предсказание цены.

**Принципы:**
• Качество > скорость
• Данные не теряются никогда
• Один донор = 95%
• Дубликаты → PRIMARY KEY
• Битые данные → карантин

━━━━━━━━━━━━━━━━━━━━
2. АРХИТЕКТУРА
━━━━━━━━━━━━━━━━━━━━

**GitHub Actions** — логика
  cron: collect/enrich/
        detect/report

**Supabase** — данные
  18 таблиц
  Session Pooler (IPv4!)
  eu-west-1

**VPS Wispbyte** — бот
  bot_host.py, 24/7

**Telegram** — интерфейс

Связка:
Actions ↔ Supabase ↔ VPS ↔ TG

━━━━━━━━━━━━━━━━━━━━
3. ИСТОЧНИКИ — 3 УРОВНЯ
━━━━━━━━━━━━━━━━━━━━

**1) ДОНОР (95%)**
OKX — работает из GitHub,
отдаёт всё.

**2) FALLBACK (5%)**
Bitget → Gate → KuCoin → MEXC
по каждой метрике отдельно.

**3) СВЕРКА**
CoinGecko. Расхождение >0.5%
→ anomaly_log.

**Не работают:**
Binance (451), Bybit (403).
Позже VPS в EU → вернём.

━━━━━━━━━━━━━━━━━━━━
4. МЕТРИКИ
━━━━━━━━━━━━━━━━━━━━

| # | Метрика        | Таблица
|---|----------------|----------
| 1 | OHLCV 1h       | candles
| 2 | OHLCV 1d       | candles
| 3 | Funding 8h     | funding
| 4 | Open Interest  | open_int
| 5 | Long/Short     | long_short
| 6 | Taker flow     | taker
| + | Market context | mkt_ctx

Монеты: BTC, ETH.
Позже: liquidations, on-chain.

━━━━━━━━━━━━━━━━━━━━
5. СХЕМА БД — 18 ТАБЛИЦ
━━━━━━━━━━━━━━━━━━━━

**RAW (90 дней):**
candles, funding_rates,
open_interest, long_short_ratio,
taker_flow

**CONTEXT:**
market_context

**DERIVED (навсегда):**
features_hourly, price_patterns,
events, causal_links

**ML:**
predictions, ml_models

**AUDIT:**
collect_log, rejected_data,
cross_check, anomaly_log,
retention_log

Схема: crypto/schema.sql

━━━━━━━━━━━━━━━━━━━━
6. СТРУКТУРА crypto/
━━━━━━━━━━━━━━━━━━━━
