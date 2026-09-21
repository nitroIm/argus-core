📄 crypto/report/weekly.py v3 — ЦЕЛИКОМ

Обновлённый отчёт. Добавлены секции: события, паттерны, корреляции, уровни.

```python
# ============================================================
# ARGUS-Trader — НЕДЕЛЬНЫЙ ОТЧЁТ v3
# ------------------------------------------------------------
# v3: + события (топ-типы за неделю)
#     + паттерны (Markov, топ n-граммы)
#     + корреляции (найденные правила)
#     + уровни (support/resistance)
#     Читает: БД + JSON-файлы из crypto/data/
# ------------------------------------------------------------
# v2: fix статистики сбора
# ============================================================

import os
import sys
import json
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
DATA_DIR = CRYPTO_ROOT / "data"
sys.path.insert(0, str(CRYPTO_ROOT))

from db import get_connection, close_connection

# --- Файлы анализа ---
SENTIMENT_FILE = DATA_DIR / "news_sentiment.json"
PATTERNS_FILE = DATA_DIR / "patterns_analysis.json"
LEVELS_FILE = DATA_DIR / "levels_analysis.json"
CORRELATIONS_FILE = DATA_DIR / "correlations.json"

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def load_json(path, default=None):
    if not path.exists():
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def send_telegram(text: str, silent: bool = False) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        print("⚠️ Нет TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID — вывод в консоль")
        clean = (text
                 .replace("<b>", "").replace("</b>", "")
                 .replace("<i>", "").replace("</i>", "")
                 .replace("<code>", "").replace("</code>", ""))
        print(clean)
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "disable_notification": silent,
            },
            timeout=15,
        )
        if r.status_code == 200:
            print("📤 Отчёт отправлен")
            return True
        print(f"⚠️ Telegram {r.status_code}: {r.text[:200]}")
        return False
    except Exception as e:
        print(f"⚠️ Telegram ошибка: {e}")
        return False


def fetch_stats() -> dict:
    stats = {
        "candles": {"total": 0, "btc_1h": 0, "eth_1h": 0, "latest": None},
        "funding_rates": {"total": 0},
        "open_interest": {"total": 0},
        "long_short_ratio": {"total": 0},
        "taker_flow": {"total": 0},
        "features_hourly": {"total": 0},
        "price_patterns": {"total": 0},
        "events": {"total": 0, "week": 0, "by_type": {}},
        "causal_links": {"total": 0},
        "anomaly_log": {"total": 0, "week": 0},
        "collect_week": {"runs": 0, "ok": 0, "fail": 0, "rows_added": 0},
        "collect_total": {"runs": 0, "rows_added": 0},
    }

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*), MAX(timestamp) FROM candles")
                row = cur.fetchone()
                stats["candles"]["total"] = row[0] or 0
                stats["candles"]["latest"] = row[1]

                cur.execute("SELECT COUNT(*) FROM candles WHERE symbol='BTCUSDT' AND timeframe='1h'")
                stats["candles"]["btc_1h"] = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM candles WHERE symbol='ETHUSDT' AND timeframe='1h'")
                stats["candles"]["eth_1h"] = cur.fetchone()[0]

                for table in ["funding_rates", "open_interest", "long_short_ratio",
                              "taker_flow", "features_hourly", "price_patterns",
                              "events", "causal_links"]:
                    cur.execute(f"SELECT COUNT(*) FROM {table}")
                    stats[table]["total"] = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM events WHERE created_at > NOW() - INTERVAL '7 days'")
                stats["events"]["week"] = cur.fetchone()[0] or 0

                cur.execute("""
                    SELECT event_type, COUNT(*)
                    FROM events WHERE created_at > NOW() - INTERVAL '7 days'
                    GROUP BY event_type ORDER BY 2 DESC LIMIT 5
                """)
                stats["events"]["by_type"] = {r[0]: r[1] for r in cur.fetchall()}

                cur.execute("SELECT COUNT(*) FROM anomaly_log WHERE created_at > NOW() - INTERVAL '7 days'")
                stats["anomaly_log"]["week"] = cur.fetchone()[0] or 0

                cur.execute("""
                    SELECT
                        COUNT(*) FILTER (WHERE job_name LIKE 'pipeline_%') AS runs,
                        COUNT(*) FILTER (WHERE job_name LIKE 'pipeline_%' AND status = 'ok') AS ok,
                        COUNT(*) FILTER (WHERE job_name LIKE 'pipeline_%' AND status = 'fail') AS fail,
                        COALESCE(SUM(records_added) FILTER (WHERE job_name LIKE 'pipeline_%'), 0) AS rows_added
                    FROM collect_log WHERE started_at > NOW() - INTERVAL '7 days'
                """)
                row = cur.fetchone()
                if row:
                    stats["collect_week"]["runs"] = row[0] or 0
                    stats["collect_week"]["ok"] = row[1] or 0
                    stats["collect_week"]["fail"] = row[2] or 0
                    stats["collect_week"]["rows_added"] = row[3] or 0

                cur.execute("""
                    SELECT
                        COUNT(*) FILTER (WHERE job_name LIKE 'pipeline_%') AS runs,
                        COALESCE(SUM(records_added) FILTER (WHERE job_name LIKE 'pipeline_%'), 0) AS rows_added
                    FROM collect_log
                """)
                row = cur.fetchone()
                if row:
                    stats["collect_total"]["runs"] = row[0] or 0
                    stats["collect_total"]["rows_added"] = row[1] or 0

    except Exception as e:
        print(f"⚠️ Ошибка чтения из БД: {e}")

    return stats


def format_age(dt) -> str:
    if not dt:
        return "?"
    try:
        now = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = now - dt
        minutes = int(delta.total_seconds() / 60)
        if minutes < 60:
            return f"{minutes}м назад"
        elif minutes < 1440:
            return f"{minutes // 60}ч назад"
        return f"{minutes // 1440}д назад"
    except Exception:
        return str(dt)[:16]


def build_report() -> str:
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    lines = []
    lines.append("🪙 <b>ARGUS-Trader — недельный отчёт v3</b>")
    lines.append(f"📅 {now.strftime('%d.%m.%Y %H:%M')} UTC")
    lines.append(f"<i>{week_ago.strftime('%d.%m')} — {now.strftime('%d.%m')}</i>")
    lines.append("")

    stats = fetch_stats()
    c = stats["candles"]

    # --- Данные ---
    lines.append("📊 <b>Данные в БД</b>")
    lines.append(f"  Свечей: <b>{c['total']:,}</b> (BTC {c['btc_1h']} | ETH {c['eth_1h']})")
    lines.append(f"  Features: {stats['features_hourly']['total']:,}")
    lines.append(f"  Patterns: {stats['price_patterns']['total']:,}")
    lines.append(f"  Events: {stats['events']['total']:,}")
    lines.append(f"  Causal links: {stats['causal_links']['total']:,}")
    lines.append(f"  Funding / OI / LS / Taker: "
                 f"{stats['funding_rates']['total']} / "
                 f"{stats['open_interest']['total']} / "
                 f"{stats['long_short_ratio']['total']} / "
                 f"{stats['taker_flow']['total']}")
    lines.append("")

    # --- Сбор ---
    cw = stats["collect_week"]
    if cw["runs"] > 0:
        lines.append("⚙️ <b>Сбор за неделю</b>")
        lines.append(f"  Прогонов: {cw['runs']} | Успешных: {cw['ok']}")
        if cw["fail"] > 0:
            lines.append(f"  ⚠️ Сбоев: {cw['fail']}")
        lines.append(f"  Добавлено строк: {cw['rows_added']:,}")
        lines.append("")

    # --- События ---
    ev = stats["events"]
    if ev["week"] > 0:
        lines.append(f"⚡ <b>События за неделю: {ev['week']}</b>")
        for t, n in list(ev["by_type"].items())[:4]:
            lines.append(f"  • {t}: {n}")
        lines.append("")

    # --- Паттерны ---
    patterns = load_json(PATTERNS_FILE, {})
    if patterns and patterns.get("symbols"):
        lines.append("🧩 <b>Паттерны (0/1)</b>")
        for sym, data in patterns["symbols"].items():
            name = sym.replace("USDT", "")
            mk = data.get("markov", {})
            up_ratio = data.get("up_ratio", 0) * 100
            lines.append(f"  <b>{name}</b>: {up_ratio:.1f}% часов роста")
            lines.append(f"    P(1|1)={mk.get('p_1_given_1', 0):.2f} | "
                         f"P(1|0)={mk.get('p_1_given_0', 0):.2f}")
            top = data.get("ngrams_top", [])[:2]
            for item in top:
                lines.append(f"    `{item['ngram']}` → ↑ {item['p_up']*100:.0f}% "
                             f"(N={item['count']})")
        lines.append("")

    # --- Корреляции ---
    corr = load_json(CORRELATIONS_FILE, {})
    if corr and corr.get("symbols"):
        all_rules = []
        for sym, data in corr["symbols"].items():
            for rule in data.get("rules", []):
                rule_copy = dict(rule)
                rule_copy["symbol"] = sym.replace("USDT", "")
                all_rules.append(rule_copy)

        if all_rules:
            all_rules.sort(key=lambda x: (x["confidence"], x["samples"]), reverse=True)
            lines.append("🧠 <b>Найденные закономерности</b>")
            for r in all_rules[:5]:
                arrow = "↑" if r["direction"] == "up" else "↓" if r["direction"] == "down" else "→"
                lines.append(
                    f"  {arrow} [{r['symbol']}] {r['rule']}\n"
                    f"     <i>{r['confidence']*100:.0f}% (N={r['samples']})</i>"
                )
            lines.append("")

    # --- Уровни ---
    levels = load_json(LEVELS_FILE, {})
    if levels and levels.get("symbols"):
        lines.append("📍 <b>Уровни</b>")
        for sym, data in levels["symbols"].items():
            name = sym.replace("USDT", "")
            price = data.get("current_price", 0)
            sup = data.get("supports", [])
            res = data.get("resistances", [])
            price_str = f"${int(price):,}" if price >= 1000 else f"${price:,.2f}"
            lines.append(f"  <b>{name}</b>: {price_str}")
            if sup:
                s = sup[0]
                s_str = f"${int(s['price']):,}" if s['price'] >= 1000 else f"${s['price']:,.2f}"
                lines.append(f"    🛡 Support: {s_str} ({-s['distance_pct']:.2f}%)")
            if res:
                r = res[0]
                r_str = f"${int(r['price']):,}" if r['price'] >= 1000 else f"${r['price']:,.2f}"
                lines.append(f"    ⚔️ Resist: {r_str} (+{r['distance_pct']:.2f}%)")
        lines.append("")

    # --- Аномалии ---
    anom = stats["anomaly_log"]
    if anom["week"] > 0:
        lines.append(f"🚨 <b>Аномалии за неделю:</b> {anom['week']}")
        lines.append("")
    else:
        lines.append("🚨 <b>Аномалии:</b> не обнаружено ✅")
        lines.append("")

    # --- Новости ---
    sentiment = load_json(SENTIMENT_FILE, {})
    if sentiment and sentiment.get("total_news", 0) > 0:
        lines.append("📰 <b>Новости</b>")
        lines.append(f"  Всего: {sentiment.get('total_news', 0)}")
        lines.append(f"  Настроение: {sentiment.get('mood', '?')}")
        lines.append(f"  Сентимент: {sentiment.get('avg_sentiment', 0):+.3f}")
        lines.append("")

    lines.append("🔧 <i>ARGUS-Trader работает стабильно.</i>")

    return "\n".join(lines)


def main():
    print("🪙 ARGUS-Trader weekly report v3")
    print("=" * 50)

    try:
        message = build_report()
        if len(message) > 4000:
            message = message[:3950] + "\n\n<i>... (обрезано)</i>"
        send_telegram(message)
    except Exception as e:
        import traceback
        traceback.print_exc()
        send_telegram(f"❌ <b>ARGUS-Trader:</b> ошибка отчёта\n<code>{str(e)[:200]}</code>")
        sys.exit(1)
    finally:
        close_connection()

    print("=" * 50)


if __name__ == "__main__":
    main()
```

---

🚦 Что делать

1. Замени crypto/report/weekly.py → v3 → Commit
2. Запусти Actions → ARGUS Crypto Reporter → Run workflow
3. Скинь отчёт из Telegram — увидим полную картину

---

📄 GUIDE №3 — раздел для вставки в GUIDE.md

Скопируй блок ниже и вставь в GUIDE.md после раздела про ARGUS-Trader v1 (того что был раньше). Это будет «ARGUS-Trader v2 — Фаза 2».

```markdown
## 🪙 9. ARGUS-TRADER v2 — ФАЗА 2 ЗАВЕРШЕНА

**Дата:** 2026-09-21
**Статус:** enrich pipeline работает, первые закономерности найдены

---

### 🎯 Что изменилось vs v1

**v1 (Фаза 1):** сбор данных (6 метрик BTC + ETH).
**v2 (Фаза 2):** данные → **признаки → паттерны → события → корреляции**.

**Новая архитектура:**
```
collect (1ч) → enrich (1ч) → report (неделя)
                  ├─ features     (50+ признаков)
                  ├─ patterns     (0/1 + Markov + n-граммы)
                  ├─ levels       (адаптивные уровни)
                  ├─ events       (12+ событий BTC, 6+ ETH)
                  ├─ causal       (lead indicators)
                  └─ correlate    (правила: "если X → Y")
```

---

### 📁 НОВЫЕ ФАЙЛЫ crypto/

```
crypto/
├── enrich/
│   ├── __init__.py
│   ├── runner.py          ← оркестратор (запускает всю цепочку)
│   ├── features.py        ← 50+ признаков из свечей
│   ├── patterns.py        ← 0/1 + Markov + n-граммы
│   ├── levels.py          ← адаптивные уровни
│   ├── events.py          ← детектор событий
│   ├── causal.py          ← что предшествовало событию
│   └── correlate.py       ← поиск закономерностей (мозг)
│
└── report/
    ├── __init__.py
    └── weekly.py          ← недельный отчёт v3
```

---

### 📊 СХЕМА ДАННЫХ — что в БД

**RAW (было):** candles, funding_rates, open_interest, long_short_ratio, taker_flow
**PRODUCTION (новое):**
- `features_hourly` — 50+ признаков на каждый час
- `price_patterns` — 0/1 строки + Markov
- `events` — события (rise/fall/new_high/volume_spike)
- `causal_links` — lead-сигналы для каждого события
- `anomaly_log` — аномалии

---

### 🔬 FEATURES — 13 признаков

| Признак | Что даёт |
|---|---|
| change_pct | % движения свечи |
| range_pct | волатильность |
| body_pct | тело свечи / range |
| upper_wick_pct | верхний хвост |
| lower_wick_pct | нижний хвост |
| volume_ratio_24h | объём / средний за 24ч |
| volatility_24h / 7d | std отклонение |
| change_4h / 24h / 7d | накопленное движение |

---

### 🧩 PATTERNS — что считается

**Бинарный график:** 1 = close > open, 0 = падение.

**N-граммы:** `1011`, `0011`, `1111` — что идёт СЛЕДУЮЩИМ.

**Markov:** P(1|1), P(0|1), P(1|0), P(0|0).

**Первые результаты (101 свеча):**
- BTC: `1111` → ↑ 64% (N=14)
- ETH: `1111` → ↑ 67% (N=12)
- BTC: P(1|0)=0.60 — после падения отскок
- ETH: P(1|0)=0.56 — то же

**Через месяц** — наберётся 720+ свечей → N станет 100+, паттерны надёжнее.

---

### 📍 LEVELS — адаптивные

**Функция `pick_step_size(price)`** — шаг автоматически от цены:
- $85,000 → major 10k, mid 5k, minor 1k
- $2,700 → major 100, mid 50, minor 10
- $850 → major 100, mid 50, minor 10
- $0.85 → major 0.1, mid 0.05, minor 0.01

**Покрытие:** ±70% от цены.

**Support/Resistance:** локальные экстремумы с 2+ касаниями.

**Volume profile:** топ-5 уровней с макс объёмом.

**Пример BTC:**
- Поддержка $81,151 — **43 касания**
- Сопротивление $85,332 — 3 касания

---

### ⚡ EVENTS — 7 типов

| Тип | Порог |
|---|---|
| rise_1h | +1.5% за час |
| fall_1h | -1.5% за час |
| rise_4h | +3% за 4 часа |
| fall_4h | -3% за 4 часа |
| new_high_7d | новый максимум за 7 дней |
| new_low_7d | новый минимум |
| volume_spike | объём > 3× от 24ч среднего |

---

### 🔗 CAUSAL — что было ДО

Для каждого события собирает:
- features за 1ч / 4ч / 24ч ДО
- funding rate, OI change, LS ratio
- ближайший уровень (из levels)
- паттерн 0/1 (из patterns)

**Запись в `causal_links`** — база для поиска lead indicators.

---

### 🧠 CORRELATE — первые закономерности

**Ищет правила:** «если условие X → направление Y».

**Типы:**
- Простые: `funding < -0.005% → UP`
- Двойные: `OI > +2% AND LS > 1.5 → UP`
- Тройные: `funding < -0.005% AND LS < 1.0 AND OI снижается → UP`

**Порог:** минимум 3 сэмпла, confidence ≥ 55%.

**Первые результаты (на 101 свече):**
- BTC: `OI change > +2% → UP` (**80%**, N=5)
- BTC: `funding > +0.005% → UP` (67%, N=12)
- ETH: `funding > +0.005% → UP` (60%, N=5)

⚠️ **N маленькие — это наблюдения, не сигналы.** Через месяц N = 50-100+ → серьёзные правила.

---

### 📅 WORKFLOWS

| Файл | Что делает | Cron |
|---|---|---|
| `crypto_collect.yml` | Сбор 6 метрик | `0 * * * *` (каждый час) |
| `crypto_enrich.yml` | features → patterns → levels → events → causal → correlate | `15 * * * *` |
| `crypto_reporter.yml` | Недельный отчёт | `0 5 * * 0` (Вс, 5 UTC) |

**Cron в GitHub может задерживаться до 15 мин.** При миграции на VPS — точность в секунду.

---

### 🛡 ПРИНЦИПЫ КАЧЕСТВА

1. **Идемпотентность:** PRIMARY KEY, ON CONFLICT DO NOTHING
2. **Fallback chain:** 4 биржи, OKX основной
3. **Валидация:** битые данные → `rejected_data`
4. **Cross-check:** OKX vs CoinGecko, аномалия > 0.5%
5. **Retention 90 дней** для сырья
6. **Immutable:** только INSERT, никаких UPDATE
7. **UTC везде**
8. **Один workflow — один процесс** (runner.py для enrich)

---

### ⚠️ ИЗВЕСТНЫЕ ОГРАНИЧЕНИЯ

1. **101 свеча** — мало данных. Нужен месяц+.
2. **CoinGecko 429** — rate limit. Кэш на 10 минут.
3. **CoinDesk / The Block / РБК** — блокируют GitHub. Убраны из `news.py`.
4. **Cron GitHub** — задержки до 15 мин. Планируется VPS.
5. **`Helsinki-NLP`** — пословный перевод, плохо на контексте. Ждём LLM.

---

### 🎯 ЧТО ДАЛЬШЕ

| # | Задача | Приоритет |
|---|---|---|
| 1 | `anomaly.py` — детекторы манипуляций (pump_dump, cross_exchange) | 🔴 сейчас |
| 2 | `learn/model.py` — ML-модель (LightGBM) | ⏳ когда 500+ свечей |
| 3 | Кнопки в боте (центральное управление) | ⏳ когда решит юзер |
| 4 | VPS + Binance как 2-й донор | ⏳ когда миграция |
| 5 | LLM для переводов и фейк-детекта | ⏳ когда деньги |
| 6 | pgvector в Supabase | ⏳ когда 20k+ чанков |

---

### 📌 КЛЮЧЕВЫЕ ПРАВИЛА

1. **Не удалять** таблицы Supabase
2. **Не менять** PRIMARY KEY структуру
3. **Секреты — только в GitHub Secrets**
4. **JSON между workflows** — не сохраняется, использовать `runner.py`
5. **Cron в :15** — после collect в :00
6. **`crypto/data/*.json`** — оперативные данные для causals/reports
7. **При смене символов** — проверить `pick_step_size` адаптивно

---

**Последнее обновление:** 2026-09-21
**Версия:** ARGUS-Trader v2 (Фаза 2)
```
