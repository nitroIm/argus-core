# argus-core
ARGUS — Autonomous Research &amp; Generative Unified System. Самообучающаяся система: читает книги, собирает данные с бирж, находит паттерны, делает прогнозы. Растёт сама — модуль за модулем.
Сделал. Один общий файл, всё лишнее отсечено, дубли убраны.

```markdown
# 🦉 ARGUS — GUIDE (v3.1, краткий)

Autonomous Research & Generative Unified System

═══════════════════════════════════════════
1. АРХИТЕКТУРА
═══════════════════════════════════════════

Три слоя:
- Бот-хост (VPS Wispbyte) — aiogram 3.x
- Скрипты (GitHub Actions) — тяжёлое
- Данные (GitHub + Supabase)

Файлы:

bot_host.py            # Telegram-бот (пульт)

scripts/
  ingest.py            # PDF → чанки → knowledge
  train_embeddings.py  # fine-tune e5-small
  build_index.py       # FAISS
  search.py            # семантический поиск
  ask.py               # RAG /ask
  reranker.py          # Cross-Encoder
  translate.py         # EN→RU
  collector.py         # скачать PDF по URL
  sources/base.py      # адаптеры источников
  logger.py            # logs/argus.jsonl
  observer.py          # пробелы в знаниях
  analyzer.py          # оценка состояния
  proposer.py          # план действий
  actor.py             # исполнитель
  brain.py             # дирижёр
  guardian.py          # контроль качества
  explorer.py          # реактивный поиск
  scout.py             # плановый поиск

crypto/                # отдельный контур (не трогаем)
  collect.py, enrich.py, detect.py

.github/workflows/     # см. §4

Данные:
  data/knowledge.json         # НИКОГДА не удалять
  data/chunks_for_index.json  # метаданные
  data/pending_cards.json     # кандидаты
  data/scout_candidates.json  # найденные
  models/argus-embeddings/    # fine-tuned e5
  faiss.index                 # векторный индекс
  books/                      # очередь PDF

═══════════════════════════════════════════
2. ПОТОК ДАННЫХ
═══════════════════════════════════════════

RAG:
  books/*.pdf
    → ingest.py → knowledge.json
    → train_embeddings.py
    → build_index.py → faiss.index
    → ask.py → Telegram

Автономия:
  запрос → logger
    → observer → analyzer → proposer
    → actor → explorer/scout
    → scout_candidates.json

Поиск книг:
  explorer.py → scout_candidates.json
    → proposer.py → pending_cards.json
    → карточка в TG (approve:<sid>)
    → approved_download.yml
    → collector.py → books/
    → следующий /train подхватит

═══════════════════════════════════════════
3. КОМАНДЫ И КНОПКИ БОТА
═══════════════════════════════════════════

/start   — приветствие
/help    — справка
/ask Q   — вопрос по книгам (30-60с)
/stats   — книг и чанков
/train   — полный цикл обучения
/find T  — поиск книг (personal)
/findnext— следующие 5
/crypto  — меню крипто
/status  — состояние системы

Кнопки:
  approve:<sid>   — скачать кандидата
  reject:<sid>    — отклонить
  personal_dl:<i> — скачать personal PDF
  personal_reject:<i>
  personal_next:<i>
  book:audio:<n>  — озвучить
  book:del:<n>    — удалить
  confirm:train
  action:collect / enrich / detect
  report:week
  charts:show:<n>

Callback формат = approve:<sid> ДВОЕТОЧИЕ (не _).
short_id = md5(url)[:16].

═══════════════════════════════════════════
4. GITHUB ACTIONS
═══════════════════════════════════════════

Ручной запуск: Actions → Run workflow.

Workflow             | Что делает
---------------------|---------------------------
ARGUS Train Model    | ingest→train→build
ARGUS Search         | ручной поиск
ARGUS Guardian       | тест/снимок/откат
ARGUS Benchmark      | точность на эталоне
ARGUS Explorer       | поиск новых книг
ARGUS Actor          | выполнить предложения
ARGUS Analyzer       | анализ логов
ARGUS Proposer       | план действий
ARGUS Reporter       | недельный отчёт
ARGUS Collect Data   | цены BTC/ETH
ARGUS Content Post   | черновик поста
Check Token          | проверка GH_PAT

Cron (UTC!):
  Brain        */30 * * * *
  Collect Data 0 * * * *
  Benchmark    0 5 * * 0     (Вс)
  Reporter     0 9 * * 1     (Пн)

Правила cron GH:
  - Ветка main/master
  - 60 дней без коммитов → стоп
  - Задержка 1-5 мин возможна

═══════════════════════════════════════════
5. КРИТИЧНЫЕ ПРАВИЛА
═══════════════════════════════════════════

1. Секреты: GH_PAT (Actions), GITHUB_PAT (Python)
2. Пути: pathlib, SCRIPT_DIR.parent
3. Время: datetime.now(timezone.utc)
   utcnow() — ЗАПРЕЩЁН
4. chunks_for_index.json:
   [{"id","source","book","text"}, ...]
5. FAISS: IndexFlatIP + normalize=True
   Метрика: avg_score (не distance)
6. E5:
   - базовая: query: / passage:
   - fine-tuned (models/): БЕЗ префиксов
7. TG API: requests.post(json=...),
   не get(params=). Резать по 4000 с
   проверкой HTML.
8. Git в Actions:
   git add <файлы>, git diff --staged,
   git pull --rebase перед push
9. knowledge.json НИКОГДА не удалять
10. sources/ лежит в scripts/sources/
    (3 уровня вверх до корня репо)
11. Строки в коде ≤55 символов
12. Логи на английском

═══════════════════════════════════════════
6. КЛЮЧЕВЫЕ ФАКТЫ
═══════════════════════════════════════════

| Что                  | Значение         |
|----------------------|------------------|
| Метаданные чанков    | chunks_for_index |
| Формат               | список dict      |
| Знания (накопит.)    | knowledge.json   |
| Модель               | e5-small, 384d   |
| Префикс fine-tuned   | НЕТ              |
| Префикс базовой E5   | query:/passage:  |
| Callback             | approve:<sid>    |
| short_id             | md5(url)[:16]    |
| Боевой /ask          | scripts/ask.py   |
| Локальный train      | scripts/train.py |
| sources/             | scripts/sources/ |

═══════════════════════════════════════════
7. ЧТО РАБОТАЕТ / ЧТО ОСТАЛОСЬ
═══════════════════════════════════════════

✅ Инкрементальный train
✅ knowledge.json не теряет данные
✅ Explorer (arXiv, Zenodo, Crossref)
✅ Карточки в TG с ✅/❌
✅ Скачивание PDF → books/ → commit
✅ /ask с fine-tuned моделью
✅ Reranker срабатывает
✅ Защита от пустых/битых JSON

⏳ Semantic Scholar 429 — не критично
⏳ total_books считает старое
⏳ FAISS → IVFFlat при >100k чанков
⏳ SQL-миграция (см. §9)
⏳ Personal Books: второй бот

═══════════════════════════════════════════
8. ДИАГНОСТИКА
═══════════════════════════════════════════

Проблема                 → Фикс
-------------------------|------------------------
Бот не отвечает /ask     → Actions: упал job?
                           faiss.index есть?
/train обрывается        → timeout-minutes: 120
JSONDecodeError в ask    → chunks_for_index битый,
                           запусти /train
KeyError 'id' в train    → fallback md5 в v3.4
Смена модели mismatch    → build_index пересоберёт
Proposer "Всего: 0"      → v3.1 читает scout_
                           candidates.json
Кнопки TG не работают    → формат approve:<sid>
PDF не скачался          → base.py 3 уровня вверх
Git push 403             → убрать secrets.GH_PAT
                           из checkout, добавить
                           permissions: contents:write
Error 400 TG             → safe_truncate_html + json=

═══════════════════════════════════════════
9. SQL / SUPABASE (заготовка)
═══════════════════════════════════════════

Статус: не используется, для масштабирования.

Параметры:
  Платформа: Supabase Free
  Проект: Argus_db
  Регион: eu-west-1 (Ирландия)
  PG: 17.6
  Подключение: Session Pooler (IPv4!)
  Расширение pgvector: НЕ установлено
  Secret: ARGUS_DB_URL

Формат:
postgresql://postgres.<ref>:<pass>@
aws-1-eu-west-1.pooler.supabase.com:5432
/postgres

⚠️ Только Session/Transaction pooler.
   Direct = IPv6 → Actions не подключится.

Дорожная карта:
- Фаза 1: регистрация (готово)
- Фаза 2: logger → БД (при >20 МБ JSON)
- Фаза 3: brain/observer/analyzer → БД
- Фаза 4: knowledge + FAISS → pgvector
  (при >20k чанков)

Первая миграция:
  CREATE EXTENSION vector;
  CREATE TABLE books, chunks, logs;
  переписать logger.py на INSERT

═══════════════════════════════════════════
10. ЧЕК-ЛИСТ ПЕРЕД РАБОТОЙ
═══════════════════════════════════════════

- GH_PAT в Settings → Secrets
- data/knowledge.json существует
- models/argus-embeddings/ существует
- faiss.index и chunks_for_index.json есть
- Ветка main/master
- books/ с .gitkeep (для git add -f)

═══════════════════════════════════════════
11. ПРОМПТ ДЛЯ НОВОГО ЧАТА
═══════════════════════════════════════════

Скопируй в новый чат:

---
Привет! Разрабатываем ARGUS — автономный
ИИ-агент (книги + крипто + personal books).

Архитектура:
  aiogram 3.x (bot_host на VPS) +
  GitHub Actions (тяжёлые скрипты) +
  FAISS + sentence-transformers.

Репо:
  nitroIm/argus-core (публичный)
  nitroIm/personal-books (публичный)

КРИТИЧНЫЕ ПРАВИЛА:
1. Секреты: GH_PAT (Actions),
   GITHUB_PAT (Python).
2. pathlib, SCRIPT_DIR.parent.
3. datetime.now(timezone.utc).
4. chunks_for_index.json =
   [{id,source,book,text}].
5. FAISS IndexFlatIP + normalize.
   avg_score (не distance).
6. E5 fine-tuned БЕЗ префиксов,
   базовая с query:/passage:.
7. TG: requests.post(json=),
   резка 4000 с HTML-проверкой.
8. git add конкретные файлы,
   pull --rebase перед push.
9. Строки в коде ≤55.
10. Файлы давать ЦЕЛИКОМ.
11. Логи на английском.

Текущее состояние: ядро готово.
Следующая задача: [ВСТАВЬ СЮДА]
---

