ты# 🦉 ARGUS — Полное Руководство (GUIDE.md)

**Autonomous Research & Generative Unified System**  
Версия системы: v3.0 (Стабильная, синхронизированная, отказоустойчивая)

---

## 📍 1. АРХИТЕКТУРА И КАРТА ФАЙЛОВ

Система разделена на три логические части: Бот-хост (VPS), Скрипты логики (GitHub Actions) и Данные.

### 📱 Пульт управления (VPS / Локально)
- `bot_host.py` — Телеграм-бот (aiogram 3.x). Принимает команды, отправляет `repository_dispatch` в GitHub, обрабатывает кнопки.

### 🧠 Ядро ИИ и Поиска (`scripts/`)
- `ingest.py` — Читает PDF/TXT из папки `books/`, разбивает на чанки → `knowledge.json`.
- `train_embeddings.py` — Дообучает модель `multilingual-e5-small` на твоих данных.
- `build_index.py` — Строит векторный индекс FAISS (`faiss.index`) и метаданные (`chunks_metadata.json`).
- `search.py` — Семантический поиск (выдает JSON для бота/GitHub).
- `semantic_search.py` — Ручной поиск через терминал (для тестов и отладки).
- `ask.py` — Обработка запроса `/ask` (RAG, перевод, логирование).
- `reranker.py` — Точная пересортировка топ-результатов через Cross-Encoder.
- `translate.py` — Локальный переводчик EN→RU (Helsinki-NLP) с кэшем.

### 📊 Сбор Данных и Контент (`scripts/`)
- `collect.py` — Собирает **актуальные цены** (MEXC→Binance→Bybit→OKX→CoinGecko) и Fear & Greed.
- `collect_data.py` — Собирает **исторические свечи** (OHLCV) для бэктестов.
- `collector.py` — Скачивает PDF-книги по URL (использует адаптеры из папки `sources/`).
- `news_analyzer.py` — Парсит 7 RSS-лент, взвешивает сентимент, дедуплицирует, переводит.
- `news_report.py` — Формирует и отправляет красивый отчет по новостям в Telegram.
- `content_generator.py` — Генерирует посты (утро/философия/инсайт) на основе книг и новостей.
- `publish_post.py` — Публикует черновики в Telegram-канал и VK (с очисткой HTML).

### 🔄 Автономность и Анализ (`scripts/`)
- `logger.py` — Записывает все действия в `logs/argus.jsonl`.
- `observer.py` — Анализирует логи, находит "пробелы" в знаниях → `observation.json`.
- `analyzer.py` — Оценивает состояние системы → `analysis.json`.
- `proposer.py` — Формирует план действий (приоритизированный) → `proposals.json`.
- `actor.py` — Исполнитель: скачивает книги, перезапускает индексацию по плану.
- `brain.py` — Дирижер: запускается по расписанию, координирует observer → analyzer → proposer → actor.
- `guardian.py` — Хранитель качества: делает снапшоты индекса, тестирует точность, делает откат при деградации.
- `explorer.py` — Реактивный поиск книг по конкретным темам (пробелам).
- `scout.py` — Плановый фоновый поиск книг по широким темам.

### ⚙️ CI/CD (`.github/workflows/`)
- `ask.yml` / `search.yml` — Обработка поисковых запросов.
- `train_model.yml` — Полный цикл обучения (с проверкой fingerprint, чтобы не тратить время зря).
- `actor.yml`, `brain.yml`, `explorer.yml`, `proposer.yml` — Циклы автономности.
- `approved_download.yml` — Скачивание одобренной книги.
- `guardian.yml`, `benchmark.yml`, `reporter.yml` — Контроль качества и отчеты.

---

## 🔄 2. ПОТОК ДАННЫХ

**Поток знаний (RAG):**  
`books/*.pdf` → `ingest.py` → `knowledge.json` → `train_embeddings.py` → `build_index.py` → `faiss.index` → `search.py` → Ответ в Telegram.

**Поток автономного улучшения:**  
Запрос пользователя → `logger.py` (запись) → `observer.py` (статистика) → `analyzer.py` (поиск проблем) → `proposer.py` (план) → `actor.py` (действие: например, запуск `explorer.py` для поиска новой книги).

**Поток контента:**  
RSS/API → `news_analyzer.py` / `collect.py` → `content_generator.py` (черновик) → Кнопка в Telegram → `publish_post.py` → TG/VK.

---

## 🤖 3. КОМАНДЫ БОТА

- `/start` — Приветствие и базовая информация.
- `/help` — Список доступных команд.
- `/ask <вопрос>` — Отправляет запрос в GitHub Actions, возвращает ответ из базы знаний.
- `/stats` — Показывает количество книг и чанков в базе.
- `/train` — Принудительный запуск полного цикла обучения (если добавлены новые книги).
- `/pending` — (Если реализовано) Показать кандидатов на скачивание.

**Интерактивные кнопки:**
- `✅ Скачать` / `❌ Отклонить` — Управление кандидатами от `explorer`/`scout`.
- `✅ Опубликовать` / `🔄 Перегенерировать` / `❌ Удалить` — Управление черновиками постов.

---

## ⏰ 4. ГАЙД ПО CRON (GitHub Actions Schedules)

Cron в GitHub Actions позволяет запускать workflows автоматически по расписанию.  
**Важно:** GitHub использует время **UTC**. Время выполнения может иметь задержку 1-5 минут при высокой нагрузке на сервера GitHub.

### Синтаксис:
`- cron: 'минута час день_месяца месяц день_недели'`

### Полезные шаблоны для ARGUS:
1. **Каждые 30 минут** (для `brain.yml`):  
   `cron: "*/30 * * * *"`
2. **Каждый час, в 00 минут** (для `collect.yml`):  
   `cron: "0 * * * *"`
3. **Каждый день в 5:00 утра по UTC** (для `actor.yml`):  
   `cron: "0 5 * * *"`
4. **Каждое воскресенье в 5:00 утра** (для `benchmark.yml`):  
   `cron: "0 5 * * 0"`
5. **Каждый понедельник в 9:00 утра** (для `reporter.yml`):  
   `cron: "0 9 * * 1"`

### ⚠️ Важные правила Cron в GitHub:
- Ветка по умолчанию должна быть `main` или `master`.
- Если в репозитории не было коммитов последние 60 дней, cron-задачи **приостанавливаются**. (Наш `brain.yml` или `collect.yml` решают эту проблему, делая регулярные коммиты).
- Для ручной проверки cron-воркфлоу всегда используй кнопку **"Run workflow"** во вкладке Actions.

---

## 🛠️ 5. ЧЕК-ЛИСТ ДИАГНОСТИКИ

| Проблема | Решение |
| :--- | :--- |
| **Бот не отвечает на `/ask`** | 1. Проверь вкладку Actions: нет ли упавших job `ARGUS Search`. <br> 2. Убедись, что `faiss.index` и `chunks_metadata.json` существуют в репо. |
| **Обучение (`/train`) обрывается** | Проверь `.github/workflows/train_model.yml`. Убедись, что `timeout-minutes: 120`. |
| **Ошибка "Нет GITHUB_PAT"** | Открой `actor.yml` и `brain.yml`. Убедись, что там написано `${{ secrets.GH_PAT }}` (именно так называется секрет в настройках репо). |
| **Telegram возвращает ошибку 400 (Bad Request)** | Исправлено в v3. Все скрипты используют `safe_truncate_html` и `requests.post` с `json=`, чтобы не рвать HTML-теги. Проверь, что ты используешь актуальные версии файлов. |
| **Поиск выдает ерунду (низкий score)** | 1. Запусти `/train`. <br> 2. Убедись, что `search.py` и `ask.py` используют `normalize_embeddings=True`. <br> 3. Проверь точность через `guardian.yml` (mode: test). |

---

## 🚀 6. ПРОМПТ ДЛЯ НОВОГО ЧАТА (СКОПИРУЙ ЭТО)

*Если этот чат закроется или контекст переполнится, скопируй текст ниже и вставь его в начало нового диалога с любым AI. Это мгновенно восстановит понимание проекта.*

```text
Привет! Мы разрабатываем автономного ИИ-агента ARGUS для трейдинга и анализа знаний.
Архитектура: aiogram 3.x (бот-хост на VPS) + GitHub Actions (тяжелые скрипты на CPU) + FAISS + sentence-transformers.

КРИТИЧЕСКИ ВАЖНЫЕ ПРАВИЛА ПРОЕКТА (СТРОГО СОБЛЮДАЙ ИХ В НОВОМ КОДЕ):
1. Секреты: В GitHub Actions используется именно имя секрета `GH_PAT` (не GITHUB_PAT). В Python-скриптах он читается как `os.getenv("GITHUB_PAT")`, а в YAML маппится как `GITHUB_PAT: ${{ secrets.GH_PAT }}`.
2. Пути: Везде используется `pathlib.Path` с абсолютными путями от `SCRIPT_DIR.parent` (корень репо). Никаких относительных `os.path.join("data", ...)`.
3. Время: Везде используется `datetime.now(timezone.utc)`. Устаревший `datetime.utcnow()` ЗАПРЕЩЕН.
4. Формат данных: Метаданные чанков хранятся в `data/chunks_metadata.json` (список словарей с ключами `id`, `source`, `book`, `text`), а не как простой список строк.
5. FAISS: Используется `IndexFlatIP` с `normalize_embeddings=True` (косинусное сходство). Метрика качества называется `avg_score` (чем выше, тем лучше), а не `avg_distance`.
6. E5 Модель: Базовая модель требует префикс "query: " для поиска. Обученная модель `models/argus-embeddings` префиксов не требует. Скрипты (`ask.py`, `build_index.py`) автоматически определяют модель и наличие префикса.
7. Telegram API: Все вызовы используют `requests.post` с параметром `json={}`, а не `get` с `params`. Обрезка текста делается по количеству элементов или с проверкой HTML-тегов, чтобы не рвать разметку `[:4000]`.
8. Git в Actions: Используется надежный паттерн: `git add <конкретные_файлы>`, проверка `git diff --staged --quiet`, и цикл `git pull --rebase --autostash` перед `git push` для избежания race conditions.

Текущий статус: Ядро системы полностью готово, синхронизировано и протестировано.
Моя следующая задача: [ВСТАВЬ СЮДА СВОЙ ЗАПРОС, НАПРИМЕР: "Добавь новый источник RSS в news_analyzer.py" или "Напиши workflow для еженедельной очистки логов"].
Жду инструкций, строго следуя правилам выше!

---

### 🎉 ПОЗДРАВЛЯЮ!

Мы проделали огромную работу. Ты теперь обладаешь **промышленным, отказоустойчивым, автономным ИИ-агентом**, код которого соответствует лучшим практикам разработки. 

1. Сохрани этот текст как `GUIDE.md` в корне репозитория.
2. Проверь, что в настройках GitHub (Settings → Secrets) лежит твой ключ под именем `GH_PAT`.
3. Закинь пару книг в папку `books/` и напиши боту `/train`.
4. А затем задай любой вопрос через `/ask`.

Если в будущем понадобится добавить новую фичу, просто открой этот `GUIDE.md`, скопируй **Промпт для нового чата** (раздел 6), вставь его в новый диалог, и мы продолжим с того же места, как будто ни секунды не прошло.

Удачи с ARGUS! Если что-то понадобится — ты знаешь, что делать. 🦉
## 🔧 7. ЖУРНАЛ ИЗМЕНЕНИЙ (сессия «Incremental Pipeline + Explorer Fix»)

### 📌 Что было сломано изначально

1. **`train_model.yml`** — переобучал ВСЁ каждый раз, не было инкремента.
2. **`ingest.py`** — при повторной обработке плодил дубли, удалял файлы некорректно.
3. **`ask.py`** — падал с `JSONDecodeError`, потому что искал несуществующий файл.
4. **`proposer.py`** — не читал `scout_candidates.json`, присылал «0 предложений».
5. **`sources/base.py`** — сохранял PDF в `scripts/books/` вместо `books/`.
6. **`approved_download.yml`** — `git add` не подхватывал файлы (без `-f`).
7. **`requirements.txt`** — не был запинен `tokenizers`, ломалась установка.

---

### 🎯 КЛЮЧЕВЫЕ ФАКТЫ (запомнить навсегда)

| Что | Правильное значение |
|---|---|
| Файл метаданных чанков | `data/chunks_for_index.json` |
| Формат метаданных | `[{"id": "...", "source": "...", "book": "...", "text": "..."}, ...]` |
| Файл знаний (накопительный) | `data/knowledge.json` — НИКОГДА не удалять |
| Модель эмбеддингов | fine-tuned в `models/argus-embeddings/` (e5-small, 384 dim) |
| Префикс для fine-tuned | НЕТ (не добавлять `query:` / `passage:`) |
| Префикс для базовой E5 | `"query: "` для запроса, `"passage: "` для чанков |
| Callback формат кнопок | `approve:<short_id>` и `reject:<short_id>` (через ДВОЕТОЧИЕ) |
| `short_id` | `md5(url)[:16]` |
| Боевой скрипт `/ask` | `scripts/ask.py` (не `semantic_search.py`) |
| Промпт-обёртка для локального запуска | `scripts/train.py` (запускает ingest → train → build) |
| `sources/` лежит в | `scripts/sources/` (вложена в scripts, не в корне!) |

---

### 📁 ФИНАЛЬНАЯ СТРУКТУРА ФАЙЛОВ
📖 Полный отчёт для GUIDE.md

Ниже — готовый блок. Копируй целиком и вставляй в свой GUIDE.md как новый раздел (например, в конец, перед «Промптом для нового чата»).

---

```markdown
## 🔧 7. ЖУРНАЛ ИЗМЕНЕНИЙ (сессия «Incremental Pipeline + Explorer Fix»)

### 📌 Что было сломано изначально

1. **`train_model.yml`** — переобучал ВСЁ каждый раз, не было инкремента.
2. **`ingest.py`** — при повторной обработке плодил дубли, удалял файлы некорректно.
3. **`ask.py`** — падал с `JSONDecodeError`, потому что искал несуществующий файл.
4. **`proposer.py`** — не читал `scout_candidates.json`, присылал «0 предложений».
5. **`sources/base.py`** — сохранял PDF в `scripts/books/` вместо `books/`.
6. **`approved_download.yml`** — `git add` не подхватывал файлы (без `-f`).
7. **`requirements.txt`** — не был запинен `tokenizers`, ломалась установка.

---

### 🎯 КЛЮЧЕВЫЕ ФАКТЫ (запомнить навсегда)

| Что | Правильное значение |
|---|---|
| Файл метаданных чанков | `data/chunks_for_index.json` |
| Формат метаданных | `[{"id": "...", "source": "...", "book": "...", "text": "..."}, ...]` |
| Файл знаний (накопительный) | `data/knowledge.json` — НИКОГДА не удалять |
| Модель эмбеддингов | fine-tuned в `models/argus-embeddings/` (e5-small, 384 dim) |
| Префикс для fine-tuned | НЕТ (не добавлять `query:` / `passage:`) |
| Префикс для базовой E5 | `"query: "` для запроса, `"passage: "` для чанков |
| Callback формат кнопок | `approve:<short_id>` и `reject:<short_id>` (через ДВОЕТОЧИЕ) |
| `short_id` | `md5(url)[:16]` |
| Боевой скрипт `/ask` | `scripts/ask.py` (не `semantic_search.py`) |
| Промпт-обёртка для локального запуска | `scripts/train.py` (запускает ingest → train → build) |
| `sources/` лежит в | `scripts/sources/` (вложена в scripts, не в корне!) |

---

### 📁 ФИНАЛЬНАЯ СТРУКТУРА ФАЙЛОВ

```

.github/workflows/
├── ask.yml                      # вызов: python scripts/ask.py
├── train_model.yml              # ⚙️ ПЕРЕПИСАН под incremental
├── approved_download.yml        # ⚙️ ПЕРЕПИСАН (git add -f, диагностика)
├── explorer.yml                 # без изменений
├── proposer.yml                 # без изменений
└── ... (остальные)

scripts/
├── ingest.py                    # ✅ v7.3 (incremental merge + pathlib)
├── train_embeddings.py          # ✅ v3.4 (fallback id, frozen inference)
├── build_index.py               # ✅ v3.4 (chunks_for_index.json)
├── ask.py                       # ✅ v6 (chunks_for_index, fix reranker API)
├── proposer.py                  # ✅ v3.1 (кнопки с callback approve:<sid>)
├── approve_handler.py           # ✅ v6 (короткий, поддержка short_id)
├── collector.py                 # без изменений
├── reranker.py                  # без изменений (совместим)
├── train.py                     # без изменений (обёртка)
├── requirements.txt             # ✅ v2 (+ tokenizers==0.19.1)
└── sources/
└── base.py                  # ✅ v3 (FIX: 3 уровня вверх для REPO_ROOT)

```

---

### 🔧 ЧТО ИМЕННО ИЗМЕНЕНО В КАЖДОМ ФАЙЛЕ

#### 1. `.github/workflows/train_model.yml` — переписан
- **Было:** `rm faiss.index`, `rm chunks_metadata.json`, пересбор всего
- **Стало:** fingerprint по книгам, инкрементальный pipeline:
  - Step 1: `Check for new books` (md5 файлов в `books/`)
  - Step 2: `python scripts/ingest.py` (merge, не перезапись)
  - Step 3: `python scripts/train_embeddings.py` (только новые чанки)
  - Step 4: `python scripts/build_index.py` (append в FAISS)
  - Step 5-8: summary, fingerprint, cleanup, commit
- **Ключевое:** `torch` устанавливается только через `requirements.txt` (не отдельно), `--extra-index-url` для CPU-колёс.

#### 2. `scripts/ingest.py` v7.3
- `pathlib.Path` вместо `os.path`
- Поле `file_hash` (md5 содержимого) — защита от дублей при переименовании
- `chunk_id = f"{fhash[:8]}#{idx:05d}"` — стабильный уникальный ID
- Дубли текста отсекаются по `md5(text)`
- `last_ingest.json` — список файлов текущего прогона (для безопасного удаления)
- Файл помечается обработанным **всегда**, если парсинг успешен (даже если 0 новых чанков)

#### 3. `scripts/train_embeddings.py` v3.4
- **Frozen inference** — модель НЕ переобучается
- Авто-детект: `models/argus-embeddings/` существует → используем её без префикса
- Иначе — `intfloat/multilingual-e5-small` с префиксом `passage:`
- Считает эмбеддинги **только для новых чанков** (не в `chunks_for_index.json`)
- **Fallback:** если у чанка нет `id` — генерирует `f"auto#{md5(text)[:12]}"`
- Пишет `model_info.json` с префиксами для `ask.py`

#### 4. `scripts/build_index.py` v3.4
- **Append** в существующий `faiss.index` (не пересоздание)
- Формат `chunks_for_index.json` = список словарей (совместим с `ask.py`)
- Защита от рассинхрона: если `index.ntotal != len(metadata)` → полный пересбор
- Legacy-формат (список строк) → полный пересбор
- Fallback `ensure_chunk_id()` — как в train

#### 5. `scripts/ask.py` v6
- Читает `data/chunks_for_index.json` (правильное имя!)
- Безопасное чтение JSON: пустой файл / битый / не список / legacy → понятные ошибки, не падение
- Читает `model_info.json` для определения префикса
- **Reranker API исправлен:** передаёт словари, получает словари с `rerank_score` и `_orig_index`
- Фильтр: `if all(c["score"] < 0.3)` → «ничего не найдено»

#### 6. `scripts/proposer.py` v3.1 — ГЛАВНОЕ ИЗМЕНЕНИЕ
- **Было:** читал только `analysis.json` → 0 предложений
- **Стало:** + читает `scout_candidates.json`, берёт 5 свежих
- Создаёт `data/pending_cards.json` = `{short_id: {url, title, topic, ...}}`
- Пишет `data/sent_candidates.json` (анти-спам, чтобы не слать повторно)
- **Отправляет в Telegram** с inline-кнопками:
  - `callback_data = "approve:<sid>"` и `"reject:<sid>"` (через двоеточие!)
  - Совместимо с `bot_host.py`

#### 7. `.github/workflows/approved_download.yml` — переписан
- `git add -f books/` — force, игнорирует `.gitignore`
- Шаг `Ensure books/ tracked` — создаёт `.gitkeep`, если нет
- Шаг `Diagnose after download` — выводит `ls books/`, `du -sh`, `git status`
- `APPROVE_ID` читает `client_payload.short_id` **первым** (совместимость с `bot_host`)
- Убран `token: ${{ secrets.GH_PAT }}` из checkout (достаточно `GITHUB_TOKEN` + `permissions: contents: write`)

#### 8. `scripts/approve_handler.py` v6
- Поддержка `short_id` от `bot_host.py`
- Fallback: если `short_id` не в `pending` → ищет в `scout_candidates.json` по `md5(url)[:16]`
- Проверка успеха: `returncode == 0 AND (is_downloaded OR has_new_file)`
- Сравнивает `books/` до и после — надёжная проверка
- Явные логи `=== collector stdout ===`

#### 9. `scripts/sources/base.py` v3 — КРИТИЧНЫЙ ФИКС
- **Было:** `os.path.dirname(os.path.dirname(...))` — 2 уровня → сохранял в `scripts/books/`
- **Стало:** `pathlib`, **3 уровня вверх** от `__file__`:
  ```python
  SCRIPT_DIR = Path(__file__).resolve().parent   # .../scripts/sources
  SCRIPTS_DIR = SCRIPT_DIR.parent                # .../scripts
  REPO_ROOT = SCRIPTS_DIR.parent                 # .../argus-core
  BOOKS_DIR = REPO_ROOT / "books"                # .../argus-core/books ✅
```

· Проверка: в логе должно быть .../argus-core/argus-core/books/... — без scripts/.

10. scripts/requirements.txt v2

· ➕ tokenizers==0.19.1 (пин для transformers==4.41.2)
· Остальное без изменений (torch 2.2.0, sentence-transformers 3.0.1, faiss-cpu 1.8.0, numpy 1.26.4)

---

🔄 КАК РАБОТАЕТ ПОЛНАЯ ЦЕПОЧКА

📚 Поиск книг (Explorer + Proposer + Approved Download)

```
1. explorer.yml → explorer.py
   └─ ищет по arXiv/Zenodo/Crossref
   └─ пишет в data/scout_candidates.json (только добавляет)

2. proposer.yml → proposer.py v3.1
   └─ читает analysis.json + scout_candidates.json
   └─ берёт 5 свежих (не в pending и не в sent)
   └─ создаёт data/pending_cards.json {sid: {url, title, ...}}
   └─ шлёт в Telegram карточки с кнопками [✅ Скачать] [❌ Пропустить]
   └─ пишет data/sent_candidates.json (анти-спам)

3. Ты тапаешь ✅ → bot_host.py ловит callback "approve:<sid>"
   └─ send_dispatch("approved_download", {"short_id": sid})

4. approved_download.yml → approve_handler.py v6
   └─ ищет url в pending_cards.json по sid
   └─ вызывает collector.py <url>
   └─ collector → source adapter → скачивает в books/
   └─ git add -f books/ + commit + push

5. Следующий /train → ingest.py → train_embeddings.py → build_index.py
   └─ новая книга попадает в knowledge.json и FAISS
```

🔍 Поиск по базе (Ask)

```
bot_host.py /ask → dispatch "run_search"
   ↓
ask.yml → python scripts/ask.py
   ↓
1. Читает chunks_for_index.json
2. Определяет модель (fine-tuned или base) → префикс
3. Эмбеддит запрос → FAISS → top-20
4. Reranker → top-5
5. Шлёт в Telegram
```

🎓 Обучение (Train)

```
/train → dispatch "run_train"
   ↓
train_model.yml
   ↓
1. Check for new books (md5 fingerprint)
2. ingest.py (merge новых в knowledge.json)
3. train_embeddings.py (эмбеддит ТОЛЬКО новые чанки)
4. build_index.py (append в faiss.index)
5. Cleanup: удаляет PDF из books/ (успешно обработанные)
6. Commit + push
```

---

⚠️ КРИТИЧНЫЕ ПРАВИЛА (не нарушать)

1. НЕ УДАЛЯТЬ data/knowledge.json — там все знания, восстанавливается только переобработкой.
2. НЕ УДАЛЯТЬ models/argus-embeddings/ — рабочая fine-tuned модель.
3. НЕ МЕНЯТЬ имя файла chunks_for_index.json — от него зависит ask.py и build_index.py.
4. При смене модели (fine-tuned ↔ base) — train_embeddings.py сам задетектит и пересоберёт индекс.
5. ingest.py никогда не удаляет записи из knowledge.json, только добавляет. Статистика total_books может показывать старые книги — это нормально.
6. git add в workflow для books/ — обязательно с -f, иначе .gitignore или git-настройки могут заблокировать.
7. Callback формат — approve:<sid> через двоеточие, не approve_<sid>.
8. Префиксы E5 — только для базовой модели. Fine-tuned из models/argus-embeddings/ префиксов НЕ требует.
9. Секреты GitHub — GH_PAT (для dispatch в workflow), GITHUB_PAT (в Python), TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.
10. sources/ лежит в scripts/sources/, не в корне репо. В base.py нужен 3 уровня вверх до корня.

---

🚦 БЫСТРАЯ ДИАГНОСТИКА

Проблема Причина Фикс
ask падает с JSONDecodeError chunks_for_index.json пуст или битый Запусти /train, проверь файл
ask падает KeyError: 'id' knowledge.json без id train_embeddings.py v3.4 — имеет fallback
Download говорит «Скачано», файла нет sources/base.py сохраняет не туда Проверь путь в логе: должен быть .../argus-core/books/... БЕЗ scripts/
Proposer присылает «Всего: 0» Читает только analysis.json proposer.py v3.1 — читает и scout_candidates.json
Кнопки в Telegram не работают Callback формат не совпадает Должно быть approve:<sid>, не approve_<sid>
Train падает KeyError: 'id' Старый knowledge.json train_embeddings.py v3.4 — fallback с md5
Train падает dimension mismatch Смена модели (384 ↔ 768) build_index.py v3.4 сам пересоберёт
Git push падает 403 GITHUB_TOKEN без прав Убрать token: secrets.GH_PAT из checkout, добавить permissions: contents: write

---

🎯 ЧТО РАБОТАЕТ СЕЙЧАС (на момент последней сессии)

· ✅ Инкрементальный train (только новые чанки)
· ✅ Накопительная база знаний (knowledge.json не теряет данные)
· ✅ Explorer находит кандидатов (arXiv, Zenodo, Crossref)
· ✅ Proposer шлёт карточки в Telegram с кнопками ✅/❌
· ✅ Кнопка ✅ → скачивание PDF в books/ → коммит
· ✅ Train подхватывает новые книги → эмбеддинги → FAISS
· ✅ /ask работает с fine-tuned моделью
· ✅ Reranker срабатывает (не fallback)
· ✅ Все скрипты имеют защиту от пустых/битых/legacy файлов

📊 Что осталось на будущее

· Semantic Scholar возвращает 429 (rate limit) — не критично, можно добавить паузу
· Статистика total_books включает старые книги (можно почистить knowledge.json вручную, но не обязательно)
· FAISS IndexFlatL2 — при >100k чанков перейти на IndexIVFFlat (пока не актуально)

```

---

## 🚦 Как добавить в GUIDE

1. Открой `GUIDE.md` в GitHub
2. **Перед** разделом «🚀 6. ПРОМПТ ДЛЯ НОВОГО ЧАТА» вставь этот блок
3. Commit

**Всё.** Теперь в GUIDE есть весь журнал. При следующем чате можешь скинуть этот кусок ИИ — и он поймёт контекст.
```
## Дорожная карта миграции на SQL

- [ ] Фаза 1: регистрация Supabase/Neon (готово)
- [ ] Фаза 2: logger.py → БД (когда knowledge.json >20 МБ)
- [ ] Фаза 3: brain/observer/analyzer → БД (при включении автономии)
- [ ] Фаза 4: knowledge + FAISS → pgvector (когда чанков >20k)