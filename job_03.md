📋 Отчёт ARGUS — 03.10.2026

🎯 Что решали

/ask не находил гайды (RSI, MACD и т.д.), хотя они были в knowledge.json. Бэкон и другие книги доминировали в поиске, гайды не попадали в топ-5.

🔍 Диагностика (по шагам)

1. Проверили knowledge.json:

· 53 книги, 8862 чанка
· Все 8 гайдов на месте (01_rsi … 08_psychology)

2. Проверили chunks_for_index.json:

· Сначала показалось, что там только Бэкон — но это был устаревший кэш на экране
· Реально: 8862 записи, все книги совпадают с knowledge.json

3. Проверили категории:

· У гайдов 01_rsi … 07_risk стояло category: "misc" вместо "trading" (баг старого ingest.py до v7.6)
· 08_psychology уже имел правильную категорию

🛠 Что исправили

Файлы workflow

.github/workflows/ask.yml

· repository_dispatch: types: [run_search] → types: [run_ask]
· Причина: два workflow (ask.yml и search.yml) слушали один тип run_search, отсюда два сообщения в TG на один запрос

.github/workflows/search.yml

· Результат поиска пишется в файл /tmp/search_result.json, читается оттуда (обход бага Expecting value)
· Отправка в TG одним сообщением, читает результат из файла

Скрипты

scripts/ingest.py v7.7 (не заменён, но обсудили)

· Добавлена логика refresh категорий для уже обработанных книг
· Автообновление категории в knowledge.json при смене папки

scripts/search.py v7 — рабочая версия

· Фикс имени файла метаданных: chunks_metadata.json → chunks_for_index.json
· Word-boundary keyword search по всему индексу (не FAISS top-500)
· Dedup по книге (одна книга = один результат)
· Регулярка (?<![a-zа-яё0-9])rsi(?![a-zа-яё0-9]) — чтобы RSIZ из C++ не ловился
· Ключевые слова: macd, rsi, bollinger, atr, volume, candle, risk, psychology
· Встроен вызов перевода (импорт из translate.py)
· Пишет JSON в файл (SEARCH_RESULT_FILE) — обход ошибки парсинга

scripts/fix_categories.py — одноразовый (создали, запустили, фикс отработал)

· 7 книг: 01_rsi … 07_risk — misc → trading
· Исправлено в knowledge.json (books + chunks) и book_categories.json

scripts/diagnose.py — одноразовый (создали для диагностики)

· Показывает, какие книги есть в knowledge.json и в chunks_for_index.json

Данные

data/knowledge.json

· 7 книг: category: "misc" → "trading"
· 18 чанков: category: "misc" → "trading"

data/book_categories.json

· 7 записей обновлено на "trading"

✅ Что подтвердилось (тесты)

/ask теперь находит гайды первым результатом:

· 01_rsi ✅
· 02_macd ✅ (3 чанка)
· 04_atr ✅ (score 1.00)
· Остальные (Bollinger, Volume, Candles, Risk, Psychology) — ждём результатов

⏳ Что осталось / не закрыли

1. Перевод английских чанков

· Импорт translate.py есть в search.py
· Но в search.yml не установлены библиотеки для модели Helsinki-NLP: torch, transformers, sentencepiece, sacremoses
· Пока не исправлено — перевод не работает (заглушка возвращает оригинал)

2. Удаление временных файлов

· scripts/fix_categories.py + .github/workflows/ARGUS_Fix_Categories.yml — можно удалить
· scripts/diagnose.py + .github/workflows/diagnose.yml — можно оставить

3. Проверка оставшихся гайдов

· 03_bollinger, 05_volume, 06_candles, 07_risk, 08_psychology — не протестированы

📚 Что дальше (по плану)

Скоро:

1. Написать 09_trend.md — готовый текст уже написан, закидывается в books/trading/
2. /train → проверка поиска
3. Написать 10_sr.md (уровни поддержки/сопротивления)
4. Продолжить по списку: 11_orderflow.md, 12_crypto.md

Позже:

5. Написать гайды по специфике биржи: типы ордеров, комиссии, funding, ликвидации (для симулятора MEXC)
6. Настроить books_reader.py — симулятор обращается к Books для анализа
7. Динамическая подстройка: симулятор сам решает, что делать с RSI/MACD, без жёстких правил

🎯 Итог дня

· Починена связка ingest → search — гайды находятся
· Починены категории — трейдинг-файлы не тонут в философии
· Убран дубль ответов в Telegram
· Убран баг с парсингом JSON в search.yml
· Убран баг с подстрокой RSIZ из C++
· Готов 09_trend.md — ждёт заливки

Библиотека готова к наполнению. Двигаемся по гайдам.