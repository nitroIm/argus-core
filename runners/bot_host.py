# ============================================================
# ARGUS — BOT HOST (ПУЛЬТ)
# Принимает команды в Telegram и отправляет их в GitHub Actions.
# Вся логика ARGUS живёт в GitHub.
# ============================================================

import os
import asyncio
import logging
import requests
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from dotenv import load_dotenv

# ---------- Загружаем переменные из .env ----------
load_dotenv()

# ---------- Настройки ----------
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
GITHUB_PAT = (os.getenv("GITHUB_PAT") or "").strip()
GITHUB_REPO = (os.getenv("GITHUB_REPO") or "").strip()

# ---------- Логирование ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# ---------- ДИАГНОСТИКА ----------
logger.info("=" * 50)
logger.info("ДИАГНОСТИКА ПЕРЕМЕННЫХ:")
logger.info(f"BOT_TOKEN: длина={len(BOT_TOKEN)}, начало={BOT_TOKEN[:15]}...")
logger.info(f"GITHUB_PAT: длина={len(GITHUB_PAT)}, начало={GITHUB_PAT[:10]}...")
logger.info(f"GITHUB_REPO: [{GITHUB_REPO}]")

# Проверяем доступ к репозиторию
try:
    test_url = f"https://api.github.com/repos/{GITHUB_REPO}"
    test_headers = {
        "Authorization": f"Bearer {GITHUB_PAT}",
        "Accept": "application/vnd.github+json"
    }
    test_response = requests.get(test_url, headers=test_headers, timeout=10)
    logger.info(f"ПРОВЕРКА РЕПОЗИТОРИЯ: статус {test_response.status_code}")
    if test_response.status_code != 200:
        logger.error(f"Ответ GitHub: {test_response.text[:300]}")
except Exception as e:
    logger.error(f"Ошибка проверки: {e}")
logger.info("=" * 50)

# ---------- Инициализация ----------
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# ============================================================
# КОМАНДА /start
# ============================================================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "🏛️ <b>ARGUS</b>\n\n"
        "Autonomous Research & Generative Unified System\n\n"
        "Я — страж знаний. Задай вопрос, и я найду ответ в своей библиотеке.\n\n"
        "<b>Команды:</b>\n"
        "/ask &lt;вопрос&gt; — задать вопрос\n"
        "/stats — статистика базы знаний\n"
        "/help — помощь",
        parse_mode="HTML"
    )


# ============================================================
# КОМАНДА /help
# ============================================================
@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "📖 <b>Как пользоваться ARGUS</b>\n\n"
        "1. Напиши <code>/ask Твой вопрос</code>\n"
        "2. ARGUS поищет в базе знаний\n"
        "3. Ответ придёт в этот чат\n\n"
        "Пример:\n"
        "<code>/ask Что такое Новая Атлантида?</code>",
        parse_mode="HTML"
    )


# ============================================================
# КОМАНДА /ask
# ============================================================
@dp.message(Command("ask"))
async def cmd_ask(message: types.Message):
    # Берём текст после /ask
    query = message.text.replace("/ask", "", 1).strip()

    if not query:
        await message.answer(
            "⚠️ Напиши вопрос после команды.\n"
            "Пример: <code>/ask Что такое Новая Атлантида?</code>",
            parse_mode="HTML"
        )
        return

    # Сообщаем, что начали
    await message.answer("🔍 ARGUS ищет ответ... Придёт через 30-60 секунд.")

    # Отправляем repository_dispatch в GitHub
    url = f"https://api.github.com/repos/{GITHUB_REPO}/dispatches"

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_PAT}",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    payload = {
        "event_type": "run_search",
        "client_payload": {
            "query": query,
            "chat_id": str(message.chat.id)
        }
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)

        logger.info(f"GitHub ответил: {response.status_code}")

        if response.status_code == 204:
            logger.info(f"✅ Запрос отправлен в GitHub: {query}")
        else:
            logger.error(f"⚠️ GitHub: {response.status_code} {response.text[:200]}")
            await message.answer(
                f"⚠️ GitHub ответил ошибкой: {response.status_code}\n"
                f"{response.text[:300]}"
            )
    except Exception as e:
        logger.error(f"❌ Ошибка отправки: {e}")
        await message.answer(f"⚠️ Ошибка соединения с GitHub: {e}")


# ============================================================
# КОМАНДА /stats
# ============================================================
@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    # Читаем summary.json через raw-ссылку
    raw_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/data/summary.json"

    try:
        response = requests.get(raw_url, timeout=15)

        if response.status_code == 200:
            data = response.json()

            text = (
                f"📊 <b>Статистика ARGUS</b>\n\n"
                f"📚 Книг: {data.get('total_books', 0)}\n"
                f"📄 Чанков: {data.get('total_chunks', 0)}\n\n"
            )

            books = data.get("books", [])
            if books:
                text += "<b>Книги:</b>\n"
                for b in books[:10]:
                    text += f"• {b['file']} ({b['pages']} стр.)\n"

            await message.answer(text, parse_mode="HTML")
        else:
            await message.answer(
                "⚠️ Файл статистики не найден.\n"
                "Сначала загрузи книгу через GitHub."
            )
    except Exception as e:
        await message.answer(f"⚠️ Ошибка: {e}")


# ============================================================
# ЗАПУСК
# ============================================================
async def main():
    logger.info("🏛️ ARGUS запущен. Слушаю команды...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())