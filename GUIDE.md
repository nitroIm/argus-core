argus-core/
│
├── 📱 bot_host.py              ← Телеграм-бот (пульт управления)
│
├── 📚 scripts/
│   ├── ingest.py               ← Читает PDF/TXT книги → knowledge.json
│   ├── train_embeddings.py     ← Обучает модель эмбеддингов
│   ├── build_index.py          ← Строит FAISS-индекс для поиска
│   ├── search.py               ← Семантический поиск (для бота)
│   ├── semantic_search.py      ← Ручной поиск (для тестов)
│   ├── ask.py                  ← Обработка /ask (RAG + перевод)
│   ├── benchmark.py            ← Тест качества поиска
│   ├── guardian.py             ← Хранитель качества (snapshots)
│   │
│   ├── collector.py            ← Скачивает книги по URL (кнопка ✅)
│   ├── collect.py              ← Собирает цены BTC/ETH с бирж
│   ├── explorer.py             ← Ищет книги по темам (реактивный)
│   ├── scout.py                ← Плановый поиск книг (по расписанию)
│   │
│   ├── news_analyzer.py        ← Анализ новостей (7 источников)
│   ├── news_report.py          ← Отчёт по новостям в Telegram
│   ├── content_generator.py    ← Генерация постов (утро/философия/инсайт)
│   ├── publish_post.py         ← Публикация в TG + VK
│   │
│   ├── observer.py             ← Анализирует логи → observation.json
│   ├── analyzer.py             ← Находит проблемы → analysis.json
│   ├── proposer.py             ← Формирует план → proposals.json
│   ├── actor.py                ← Выполняет план (скачивает книги и т.д.)
│   ├── brain.py                ← Дирижёр всей системы
│   │
│   ├── reranker.py             ← Точная пересортировка результатов
│   ├── translate.py            ← Перевод EN→RU (локальная модель)
│   └── logger.py               ← Запись действий в лог
│
├──  data/
│   ├── knowledge.json          ← Все книги разбиты на чанки
│   ├── chunks_metadata.json    ← Метаданные чанков (книга, id)
│   ├── faiss.index             ← Векторный индекс для поиска
│   ├── observation.json        ← Статистика запросов
│   ├── analysis.json           ← Найденные проблемы
│   ├── proposals.json          ← План действий
│   ├── scout_candidates.json   ← Кандидаты на скачивание
│   ├── price_history.json      ← История цен BTC
│   ├── market_summary.json     ← Актуальные цены + Fear&Greed
│   └── news_sentiment.json     ← Настроение рынка
│
├── 🤖 models/
│   └── argus-embeddings/       ← Обученная модель эмбеддингов
│
└── ⚙️ .github/workflows/
    ├── train_model.yml         ← Обучение модели
    ├── search.yml              ← Обработка /ask
    ├── explorer.yml            ← Запуск explorer.py
    └── ... другие workflows
📚 КНИГИ
   ↓ ingest.py
knowledge.json → chunks_metadata.json
   ↓ build_index.py
faiss.index (векторный поиск)
   ↓ search.py / ask.py
Ответ в Telegram

🔍 ЗАПРОСЫ ПОЛЬЗОВАТЕЛЯ
   ↓ logger.py
logs/argus.jsonl
   ↓ observer.py
observation.json (статистика)
   ↓ analyzer.py
analysis.json (проблемы)
   ↓ proposer.py
proposals.json (план)
   ↓ actor.py
Действия (скачать книги, переобучить)

📰 НОВОСТИ
   ↓ news_analyzer.py
news_sentiment.json
   ↓ content_generator.py
pending_post.json (черновик)
   ↓ publish_post.py
Telegram + VK

📈 РЫНОК
   ↓ collect.py
price_history.json + market_summary.json

🧠 АВТОНОМНОСТЬ
   ↓ brain.py (каждые 20 минут)
Запускает observer → analyzer → proposer → actor
