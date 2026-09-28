📋 ОТЧЁТ ARGUS — 28.09.2026

✅ ЧТО СДЕЛАНО

1. Feature Engineering (главный результат дня)

Добавлено 12 новых признаков в features_hourly:

Признак Что считает
ema9_dist_pct расстояние до EMA9
ema21_dist_pct расстояние до EMA21
ema50_dist_pct расстояние до EMA50
macd MACD линия
macd_signal сигнальная линия
bb_upper_dist расстояние до верхней Bollinger
bb_lower_dist расстояние до нижней Bollinger
bb_width_pct ширина Bollinger
dist_high_24h_pct расстояние до 24h high
dist_low_24h_pct расстояние до 24h low
consecutive_up сколько свечей подряд растут
session 0=азия, 1=европа, 2=америка

Колонок в features_hourly: 26 → 38

2. Обновление кода

Заменены файлы:

· crypto/enrich/features.py → v5
  · Добавлены функции compute_ema, compute_macd, compute_bbands, compute_consecutive, get_session
  · SQL insert обновлён под 38 колонок
· crypto/learn/dataset.py → v5
  · INTERNAL_COLS = 33 (было 21)
  · USE_EXTERNAL = False (external выключен)
· crypto/learn/predict.py → v3
  · Читает признаки из model_meta.json
  · Больше не будет mismatch моделей
· crypto/learn/autotune.py → v3
  · MIN_EDGE = 0.02 — не сохраняет плохие параметры
  · Fix make_params (learning_rate)
· crypto/learn/train.py → v5
  · Читает best_params.json от autotune

3. Autotune — нашёл лучшие параметры

```
BEST: num_leaves=15, lr=0.03, max_depth=3
edge = +0.0388
trees = 27
```

Сохранил в crypto/learn/models/best_params.json

4. Train —_dist обучил модель на :33 признаках

```
 **Top features:**
```

37```
consecutive_up:   135.84  ← НОВЫЙ
lower_wick_pct:    84.71
bb_width_pct:      75.57  ← НОВЫЙ
hour_of_day:       68.42
bb_upper.51  ← НОВЫЙ

```

**3 из 5 топ-признаков — новые.** Feature engineering работает.

### 5. Predict + Notify — без ошибок

```

ARGUS PREDICT v3
model features: 33
BTCUSDT: prob_up=0.4916 → WAIT
ETHUSDT: prob_up=0.5038 → WAIT

```

**Никаких ошибок совместимости.**

---

## ⚠️ ЧТО ОТКАЧЕНО

### External market (DXY/SPX/GOLD)

**Было:** `USE_EXTERNAL = True`
**Стало:** `USE_EXTERNAL = False`

**Причина:** С external edge был −0.0098, без external +0.0194. External шумит.

**Важно:** Данные external **собираются** (в `external_market`), просто ML их не использует. Можно включить обратно когда данных больше.

### Вторая база Supabase

**Что делали:**
- Создали организацию `Argus_Global_data`
- Создали проект (500 МБ)
- Добавили `ARGUS_DB_URL_2` в Secrets
- Протестировали запись из GitHub — успешно
- Мигрировали 143 строки (onchain, macro, fear_greed, external, orderbook)

**Откатили:**
- DROP 5 таблиц во второй базе
- Удалили `crypto/db2.py`, `migrate_global.py`, `test_global_db.py`
- Удалили workflows

**Причина:** Слишком мало данных для разделения. Пусть всё копится в первой базе. Вернёмся когда будет 100+ МБ.

**Что осталось:** Пустой проект Supabase, `ARGUS_DB_URL_2` в Secrets. Пригодятся позже.

---

## 🔧 ЧТО ИСПРАВЛЕНО (баги)

### Баг 1: Autotune не работал

**Ошибка:** `make_params() got unexpected keyword argument 'learning_rate'`
**Фикс:** В `make_params` параметр `lr` → `learning_rate`

### Баг 2: Predict ломался

**Ошибка:** `number of features in data (21) is not the same as in training data (25)`
**Фикс:** predict v3 читает признаки из `model_meta.json`, а не из хардкода

### Баг 3: Data Audit

**Ошибка:** workflow упал на 33 попытке из-за `>` в Telegram
**Фикс:** не завершён, отложен

---

## 📊 СОСТОЯНИЕ СИСТЕМЫ

### Первая база (argus-db)

| Таблица | Строк |
|---|---|
| candles | ~680 |
| funding | ~240 |
| **features_hourly** | **514** |
| onchain_metrics | 2 |
| macro_metrics | ~30 |
| fear_greed | 2 |
| external_market | 168 |
| orderbook_snapshots | ~10 |

**features_hourly колонок:** 38 (было 26)

### Модель

- **Версия:** v5 (train), v3 (autotune, predict)
- **Признаков:** 33
- **Edge autotune:** +0.0388
- **Edge train:** +0.0097
- **Top feature:** consecutive_up

### Cron

10 задач в cron-job.org — работают без изменений.

---

## 📁 ФАЙЛЫ, КОТОРЫЕ ИЗМЕНИЛИСЬ СЕГОДНЯ

```

crypto/enrich/features.py           v4.4 → v5
crypto/learn/dataset.py             v4 → v5
crypto/learn/predict.py             v2 → v3
crypto/learn/autotune.py            v1 → v3
crypto/learn/train.py               v4 → v5
crypto/learn/models/best_params.json     СОЗДАН
crypto/learn/models/lgb_model.txt        ПЕРЕЗАПИСАН

```

**SQL:**
- `ALTER TABLE features_hourly ADD COLUMN × 12`

---

## 🎯 ЧТО ЖДЁМ

**Данные копятся:**

- Сейчас: 514 features
- +48/сутки
- Через **3-4 дня** → 700+

**Когда 700+:**

1. **Crypto Autotune** → Run
2. **Crypto Learn** → Run
3. Смотрим edge

**Ожидаем:** edge **+0.05** и выше. Тогда появятся сигналы с confidence > 0.30.

---

## ❌ ЧТО НЕ ДЕЛАЛИ

- Не добавляли монеты (SOL/BNB)
- Не меняли pipeline
- Не трогали симулятор
- Не включали external обратно
- Не запускали Data Audit fix

---

## ⏭️ ЧТО ДАЛЬШЕ

**Приоритеты:**

1. **Similarity search** — файл готов, не залит
2. **Дополнительные монеты** — только для обучения
3. **Walk-forward validation**
4. **Threshold ±0.3%** — на 700+ строках
5. **VPS + Binance** — когда деньги
6. **Автономия** — далеко впереди

**Сейчас:** ждём данных. Система работает автономно.

---

**Сессия закрыта. Всё стабильно.** 🦉
```