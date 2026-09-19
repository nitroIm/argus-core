# ============================================================
# ARGUS — BOT HOST (ПУЛЬТ УПРАВЛЕНИЯ)
# v2: фикс callback_data, безопасный HTML, **kwargs, единые заголовки GitHub
# ============================================================

import os
import asyncio
import logging
import requests
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery
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
@dp.message(Command("start"), **kwargs)
async def cmd_start(message: types.Message, **kwargs):
    await message.answer(
        "🏛️ <b>ARGUS</b>\n\n"
        "Autonomous Research & Generative Unified System\n\n"
        "Я — страж знаний. Задай вопрос, и я найду ответ в своей библиотеке.\n\n"
        "<b>Команды:</b>\n"
        "/ask &lt;вопрос&gt; — задать вопрос по базе знаний\n"
        "/stats — статистика загруженных книг\n"
        "/help — справка",
        parse_mode="HTML"
    )


# ============================================================
# КОМАНДА /help
# ============================================================
@dp.message(Command("help"), **kwargs)
async def cmd_help(message: types.Message, **kwargs):
    await message.answer(
        "📖 <b>Как пользоваться ARGUS</b>\n\n"
        "1. Напиши <code>/ask Твой вопрос</code>\n"
        "2. ARGUS передаст запрос в GitHub Actions\n"
        "3. Через 30-60 секунд ответ придёт в этот чат\n\n"
        "Пример:\n"
        "<code>/ask Что такое риск-менеджмент?</code>",
        parse_mode="HTML"
    )


# ============================================================
# КОМАНДА /ask
# ============================================================
@dp.message(Command("ask"), **kwargs)
async def cmd_ask(message: types.Message, **kwargs):
    # Берём текст после /ask
    query = message.text.replace("/ask", "", 1).strip()

    if not query:
        await message.answer(
            "⚠️ Напиши вопрос после команды.\n"
            "Пример: <code>/ask Что такое Новая Атлантида?</code>",
            parse_mode="HTML"
        )
        return

    await message.answer("🔍 <b>ARGUS ищет ответ...</b>\nОбычно это занимает 30-60 секунд.")

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
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        logger.info(f"GitHub dispatch ответил: {response.status_code}")

        if response.status_code == 204:
            logger.info(f"✅ Запрос отправлен в GitHub: {query}")
        else:
            logger.error(f"⚠️ GitHub ошибка: {response.status_code} {response.text[:200]}")
            await message.answer(
                f"⚠️ GitHub ответил ошибкой: {response.status_code}\n"
                f"<code>{response.text[:300]}</code>",
                parse_mode="HTML"
            )
    except Exception as e:
        logger.error(f"❌ Ошибка отправки: {e}")
        await message.answer(f"⚠️ Ошибка соединения с GitHub: {e}")


# ============================================================
# КОМАНДА /stats
# ============================================================
@dp.message(Command("stats"), **kwargs)
async def cmd_stats(message: types.Message, **kwargs):
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
                text += "<b>Последние книги:</b>\n"
                for b in books[:10]:
                    # Используем <code> для защиты от спецсимволов в именах файлов
                    text += f"• <code>{b['file']}</code> ({b['pages']} стр.)\n"

            await message.answer(text, parse_mode="HTML")
        else:
            await message.answer(
                "⚠️ Файл статистики не найден.\n"
                "Сначала запусти обучение командой <code>/train</code>.",
                parse_mode="HTML"
            )
    except Exception as e:
        await message.answer(f"⚠️ Ошибка получения статистики: {e}")


# ============================================================
# ОБРАБОТКА КНОПОК (Скачать / Отклонить)
# ============================================================
@dp.callback_query(F.data.startswith("approve:"), **kwargs)
async def handle_approve(callback: CallbackQuery, **kwargs):
    # Извлекаем ID после "approve:"
    short_id = callback.data.replace("approve:", "")

    url = f"https://api.github.com/repos/{GITHUB_REPO}/dispatches"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_PAT}",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    payload = {
        "event_type": "approved_download",
        "client_payload": {"short_id": short_id}
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=15)
        if r.status_code == 204:
            await callback.message.edit_text(
                callback.message.text + "\n\n✅ <b>Одобрено. Запущено скачивание...</b>",
                parse_mode="HTML"
            )
            await callback.answer("Отправлено в GitHub")
        else:
            await callback.answer("Ошибка GitHub API", show_alert=True)
    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)


@dp.callback_query(F.data.startswith("reject:"), **kwargs)
async def handle_reject(callback: CallbackQuery, **kwargs):
    short_id = callback.data.replace("reject:", "")

    await callback.message.edit_text(
        callback.message.text + "\n\n❌ <b>Отклонено пользователем</b>",
        parse_mode="HTML"
    )
    await callback.answer("Кандидат удален из очереди")


# ============================================================
# ЗАПУСК
# ============================================================
async def main():
    logger.info("🏛️ ARGUS Bot Host запущен. Слушаю команды...")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        logger.info("Бот остановлен.")


if __name__ == "__main__":
    # Для Windows/Unix совместимости при остановке через Ctrl+C
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Получен сигнал остановки (Ctrl+C)")
