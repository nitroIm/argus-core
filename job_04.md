📋 Отчёт ARGUS за 03.10.2026

🎯 Что делали сегодня

Наполняли Books-контур гайдами и чинили поиск, чтобы симулятор мог использовать знания.

---

🛠 Что исправили в коде

scripts/search.py — основная работа

Переписали полностью. Было много версий, финальная — v9.

Что было сломано:

· Файл chunks_metadata.json — не существовал, скрипт падал
· search.yml использовал старую версию без ключевых слов
· Дубли ответов в Telegram (два workflow слушали run_search)

Что стало:

· META_FILE → chunks_for_index.json (реальный файл)
· Keyword-поиск по всему индексу (8862 чанка), не топ-500 FAISS
· Слово-граница для латинских аббревиатур (rsi, macd, atr) — чтобы RSIZ из C++ не ловился
· Обычный substring для русских корней (уровен → уровень, плеч → плечо)
· Dedup по книге — одна книга = один результат
· Перевод EN→RU через translate.py
· JSON пишется в файл /tmp/search_result.json (обход ошибки парсинга)

scripts/ingest.py — v7.7 (обсудили, но не меняли)

· Логика refresh категорий для уже обработанных книг

.github/workflows/ask.yml

· repository_dispatch: types: [run_search] → types: [run_ask]
· Убрали дубль ответов

.github/workflows/search.yml

· Установка torch, transformers, sentencepiece, sacremoses
· Чтение результата из файла
· timeout-minutes: 15 (для загрузки модели перевода)

.github/workflows/ingest.yml

· permissions: contents: write
· fetch-depth: 0
· concurrency: argus-ingest
· git pull --rebase --autostash перед push
· Retry 3 раза
· [skip ci] в commit

scripts/fix_categories.py — одноразовый

· Перевёл 7 гайдов 01_rsi … 07_risk из misc в trading

scripts/diagnose.py — одноразовый

· Показал содержимое knowledge.json и chunks_for_index.json

---

📚 Гайды, написанные сегодня

Раньше (уже были в индексе):

1. 01_rsi.md ✅
2. 02_macd.md ✅
3. 03_bollinger.md ✅
4. 04_atr.md ✅
5. 05_volume.md ✅
6. 06_candles.md ✅
7. 07_risk.md ✅
8. 08_psychology.md ✅

Написаны сегодня:

9. 09_trend.md — тренд и трендовый анализ
10. 10_sr.md — уровни поддержки и сопротивления
11. 11_orderflow.md — order flow, имбаланс, stop hunting
12. 12_crypto.md — крипта: funding, OI, LS ratio, плечи
13. 13_exchange.md — как торговать на бирже, ордера, комиссии
14. 14_fibonacci.md — уровни Фибоначчи
15. 15_patterns.md — графические паттерны (H&S, треугольники)
16. 16_divergence.md — дивергенции
17. 17_money_management.md — управление капиталом
18. 18_mexc.md — специфика биржи MEXC
19. 19_binance.md — специфика биржи Binance

---

✅ Что подтверждено тестами

/ask находит первым результатом:

· 01_rsi ✅
· 02_macd ✅ (3 чанка, score 1.00)
· 04_atr ✅ (score 1.00)

Остальные (03_bollinger, 05_volume, 06_candles, 07_risk, 08_psychology) — ждут проверки.

---

🐞 Что нашли по ходу

1. Категории misc — старый ingest.py до v7.6 не читал подпапки
2. Дубль workflow — ask.yml и search.yml слушали один тип run_search
3. Ошибка парсинга JSON — warnings от transformers попадали в stdout перед JSON
4. Подстрока RSIZ — слово-граница решает
5. Русские корни с word boundary — не находили словоформы
6. Git push rejected в ingest.yml — нужно pull --rebase
7. Устаревший chunks_metadata.json — правильное имя chunks_for_index.json

---

🎯 Библиотека сейчас

· 53 книги в knowledge.json
· 8862 чанка в индексе
· 19 гайдов (8 + 11 новых)
· FAISS-индекс 13 МБ

---

⏳ Что осталось на завтра

1. Проверить новые гайды 09 … 19 через /ask
2. Написать ещё по плану:
   · 20_bybit.md
   · 21_okx.md
   · 22_kucoin.md
   · 23_bitget.md
   · 24_gate.md
   · Возможно: order books, iceberg, spoofing
3. Доделать перевод — проверить, что translate.py реально работает
4. Закрыть вопрос с категориями — если снова слетят

---

🚀 Что дальше (после 20 файлов)

1. books_reader.py — симулятор обращается к Books
2. Симулятор передаёт ситуацию → получает знание → учитывает как голос
3. Динамика — симулятор сам подбирает веса по рынку
4. Дописать отчёт симулятора
5. Работа с ML — финальная 
