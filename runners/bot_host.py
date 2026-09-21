# ============================================================
# ARGUS — BOT HOST v2.2
# ------------------------------------------------------------
# v2.2: подробный /help, кнопка "Помощь" в меню.
# v2.1: fix пустой inputs, проверка aiogram.
# ------------------------------------------------------------
# Требования: GH_PAT должен иметь scope 'workflow'
# ============================================================

import os
import sys
import asyncio
import logging
import requests

try:
    from aiogram import Bot, Dispatcher, types, F
    from aiogram.filters import Command
    from aiogram.types import CallbackQuery
    from aiogram.types import InlineKeyboardMarkup
    from aiogram.types import InlineKeyboardButton
except ImportError as e:
    print("=" * 60)
    print("❌ ОШИБКА: aiogram не установлен")
    print(f"   {e}")
    print("=" * 60)
    print("Решение на VPS:")
    print("  pip install aiogram==3.13.0")
    print("  pip install python-dotenv requests")
    print("=" * 60)
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("⚠️ python-dotenv не установлен")
    print("  pip install python-dotenv")


# ============================================================
# НАСТРОЙКИ
# ============================================================
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
GITHUB_PAT = (
    os.getenv("GH_PAT")
    or os.getenv("GITHUB_PAT")
    or ""
).strip()
GITHUB_REPO = (os.getenv("GITHUB_REPO") or "").strip()

if not BOT_TOKEN:
    print("❌ BOT_TOKEN не задан в .env")
    sys.exit(1)
if not GITHUB_REPO:
    print("❌ GITHUB_REPO не задан в .env")
    sys.exit(1)


# ============================================================
# ЛОГИ
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

logger.info("=" * 50)
logger.info(f"BOT_TOKEN: {len(BOT_TOKEN)} символов")
logger.info(f"GH_PAT: {len(GITHUB_PAT)} символов")
logger.info(f"REPO: {GITHUB_REPO}")
logger.info("=" * 50)


# ============================================================
# WORKFLOWS
# ============================================================
WORKFLOWS = {
    "train": {
        "file": "train_model.yml",
        "label": "🎓 Train ARGUS",
        "confirm": True,
    },
    "collect": {
        "file": "crypto_collect.yml",
        "label": "🪙 Collect Crypto",
        "confirm": False,
    },
    "enrich": {
        "file": "crypto_enrich.yml",
        "label": "🧠 Enrich Crypto",
        "confirm": False,
    },
    "detect": {
        "file": "crypto_detect.yml",
        "label": "🚨 Detect Anomaly",
        "confirm": False,
    },
    "report_week": {
        "file": "crypto_reporter.yml",
        "label": "📊 Отчёт за неделю",
        "confirm": False,
    },
}


# ============================================================
# GITHUB — ЗАПУСК WORKFLOW
# ============================================================
def run_workflow(key: str, inputs: dict = None) -> tuple:
    wf = WORKFLOWS.get(key)
    if not wf:
        return False, f"Неизвестный workflow: {key}"

    url = (
        f"https://api.github.com/repos/{GITHUB_REPO}"
        f"/actions/workflows/{wf['file']}/dispatches"
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_PAT}",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    data = {"ref": "main"}
    if inputs:
        data["inputs"] = inputs

    try:
        r = requests.post(url, headers=headers, json=data, timeout=15)
        if r.status_code in (204, 201, 200):
            return True, f"✅ {wf['label']} запущен"
        if r.status_code == 404:
            return False, "❌ Workflow не найден"
        if r.status_code == 403:
            return False, "❌ Нет прав (scope 'workflow')"
        if r.status_code == 422:
            return False, "❌ Ошибка параметров"
        msg = r.text[:150] if r.text else str(r.status_code)
        return False, f"❌ Ошибка: {msg}"
    except Exception as e:
        logger.error(f"run_workflow: {e}")
        return False, f"❌ {e}"


# ============================================================
# GITHUB — ЧТЕНИЕ JSON
# ============================================================
def read_json(path: str) -> dict:
    url = f"https://raw.githubusercontent.com"
    url += f"/{GITHUB_REPO}/main/{path}"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.warning(f"read_json {path}: {e}")
    return {}


# ============================================================
# LEGACY DISPATCH
# ============================================================
def send_dispatch(event_type: str, payload: dict) -> bool:
    url = f"https://api.github.com/repos/{GITHUB_REPO}/dispatches"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_PAT}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    data = {"event_type": event_type, "client_payload": payload}
    try:
        r = requests.post(url, headers=headers, json=data, timeout=15)
        return r.status_code == 204
    except Exception as e:
        logger.error(f"dispatch: {e}")
        return False


# ============================================================
# ИНИЦИАЛИЗАЦИЯ БОТА
# ============================================================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# ============================================================
# ХЕЛПЕРЫ
# ============================================================
def fmt_price(p: float) -> str:
    if p >= 1000:
        return f"${int(p):,}"
    if p >= 1:
        return f"${p:,.2f}"
    return f"${p:.4f}"


def build_argus_status() -> str:
    data = read_json("data/summary.json")
    if not data:
        return "⚠️ Нет данных ARGUS"
    books = data.get("total_books", 0)
    chunks = data.get("total_chunks", 0)
    return f"🏛️ <b>ARGUS</b>\n📚 Книг: {books}\n📄 Чанков: {chunks}"


def build_crypto_status() -> str:
    patterns = read_json("crypto/data/patterns_analysis.json")
    levels = read_json("crypto/data/levels_analysis.json")
    if not patterns and not levels:
        return "⚠️ Нет крипто-данных"
    lines = ["🪙 <b>Crypto</b>"]
    if levels and levels.get("symbols"):
        for sym, data in levels["symbols"].items():
            name = sym.replace("USDT", "")
            price = data.get("current_price", 0)
            lines.append(f"💰 <b>{name}</b>: {fmt_price(price)}")
            sup = data.get("supports", [])
            res = data.get("resistances", [])
            if sup:
                s = sup[0]
                lines.append(f"  🛡 {fmt_price(s['price'])}")
            if res:
                r = res[0]
                lines.append(f"  ⚔️ {fmt_price(r['price'])}")
    if patterns and patterns.get("symbols"):
        lines.append("")
        lines.append("🧩 <b>Паттерны</b>")
        for sym, data in patterns["symbols"].items():
            name = sym.replace("USDT", "")
            up = data.get("up_ratio", 0) * 100
            mk = data.get("markov", {})
            p10 = mk.get("p_1_given_0", 0)
            lines.append(f"  {name}: {up:.0f}% | P(1|0)={p10:.2f}")
    return "\n".join(lines)


def build_full_status() -> str:
    parts = ["📊 <b>ARGUS — СТАТУС</b>", ""]
    parts.append(build_argus_status())
    parts.append("")
    parts.append(build_crypto_status())
    return "\n".join(parts)


# ============================================================
# ТЕКСТ ПОМОЩИ
# ============================================================
def help_text() -> str:
    return (
        "📖 <b>ARGUS — ПОЛНАЯ СПРАВКА</b>\n"
        "\n"
        "🏛️ <b>Что такое ARGUS?</b>\n"
        "Система из двух модулей:\n"
        "• <b>ARGUS</b> — база знаний по книгам\n"
        "• <b>Crypto</b> — рыно/ETH\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📚 <b>КНИГИ И ПОИСК</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "/ask &lt;вопрос&gt;\n"
        "  Поиск ответа по базе знаний.\n"
        "  <i>Пример:</i> <code>/ask Что такое риск?</code>\n"
        "\n"
        "/find &lt;тема&gt;\n"
        "  Поиск книг (arXiv, Zenodo, Semantic Scholar)\n"
        "  с переводом на русский.\n"
        "  <i>Пример:</i> <code>/find stoicism</code>\n"
        "\n"
        "/findnext\n"
        "  Показать следующие 5 книг после /find\n"
        "\n"
        "/stats\n"
        "  Количество книг и чанков в базе\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🪙 <b>КРИПТО</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "/crypto\n"
        "  Меню крипто-операций (кнопки)\n"
        "\n"
        "/collect\n"
        "  Собрать свежие данные BTC/ETH\n"
        "  (запускает GitHub Actions, ~4 мин)\n"
        "\n"
        "/enrich\n"
        "  Обновить признаки, паттерны, события\n"
        "  (~1.5 мин)\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚙️ <b>УПРАВЛЕНИЕ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "/panel\n"
        "  Главное меню с кнопками\n"
        "\n"
        "/status\n"
        "  Сводка: книги, паттерны, цены, уровни\n"
        "\n"
        "/train\n"
        "  Обучение ARGUS на новых книгах\n"
        "  (5-30 минут, требует подтверждения)\n"
        "\n"
        "/audio\n"
        "  Аудиокниги (в разработке)\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🎛 <b>КНОПКИ ГЛАВНОГО МЕНЮ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "🎓 <b>Train ARGUS</b>\n"
        "  Обучение на новых книгах в books/\n"
        "  Запускает workflow train_model.yml\n"
        "\n"
        "🪙 <b>Crypto</b>\n"
        "  Подменю: Collect, Enrich, Detect\n"
        "  Сбор → анализ → детект аномалий\n"
        "\n"
        "📊 <b>Отчёты</b>\n"
        "  Недельный отчёт с графиками BTC/ETH\n"
        "  (текст + свечи + паттерн + Markov)\n"
        "\n"
        "🎵 <b>Аудиокниги</b>\n"
        "  Раздел в разработке\n"
        "  Будет озвучивать твои personal-книги\n"
        "\n"
        "⚙️ <b>Статус</b>\n"
        "  Показывает актуальные данные:\n"
        "  книги, чанки, паттерны, цены, уровни\n"
        "\n"
        "❓ <b>Помощь</b>\n"
        "  Эта справка\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 <b>СОВЕТЫ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "• Train запускается 5-30 мин,\n"
        "  результат придёт автоматически\n"
        "\n"
        "• Collect/Enrich запускаются каждый час\n"
        "  сами через cron GitHub\n"
        "\n"
        "• После /find появятся кнопки\n"
        "  [📥 1. Скачать] [📥 2. Скачать] ...\n"
        "\n"
        "• После скачивания книга уйдёт\n"
        "  в личную папку, в ARGUS не попадёт\n"
    )


# ============================================================
# КЛАВИАТУРЫ
# ============================================================
def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🎓 Train ARGUS",
                callback_data="menu:train",
            ),
            InlineKeyboardButton(
                text="🪙 Crypto",
                callback_data="menu:crypto",
            ),
        ],
        [
            InlineKeyboardButton(
                text="📊 Отчёты",
                callback_data="menu:reports",
            ),
            InlineKeyboardButton(
                text="🎵 Аудиокниги",
                callback_data="menu:audio",
            ),
        ],
        [
            InlineKeyboardButton(
                text="⚙️ Статус",
                callback_data="menu:status",
            ),
            InlineKeyboardButton(
                text="❓ Помощь",
                callback_data="menu:help",
            ),
        ],
    ])


def kb_crypto() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🪙 Collect",
                callback_data="action:collect",
            ),
            InlineKeyboardButton(
                text="🧠 Enrich",
                callback_data="action:enrich",
            ),
        ],
        [
            InlineKeyboardButton(
                text="🚨 Detect",
                callback_data="action:detect",
            ),
        ],
        [
            InlineKeyboardButton(
                text="◀️ Назад",
                callback_data="menu:main",
            ),
        ],
    ])


def kb_reports() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📊 За неделю",
                callback_data="report:week",
            ),
        ],
        [
            InlineKeyboardButton(
                text="◀️ Назад",
                callback_data="menu:main",
            ),
        ],
    ])


def kb_confirm(action_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Да, запустить",
                callback_data=f"confirm:{action_key}",
            ),
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data="menu:main",
            ),
        ],
    ])


# ============================================================
# КОМАНДЫ
# ============================================================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    text = (
        "🏛️ <b>ARGUS</b>\n"
        "Autonomous Research System\n\n"
        "Главное меню — внизу.\n"
        "Полная справка: /help"
    )
    await message.answer(
        text,
        reply_markup=kb_main(),
        parse_mode="HTML",
    )


@dp.message(Command("panel"))
async def cmd_panel(message: types.Message):
    await message.answer(
        "🏛️ <b>Центр управления</b>",
        reply_markup=kb_main(),
        parse_mode="HTML",
    )


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    text = help_text()
    # Если слишком длинно — режем
    if len(text) > 4000:
        text = text[:3950] + "\n\n<i>...обрезано</i>"
    await message.answer(
        text,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    await message.answer("⏳ Читаю статус...")
    text = build_full_status()
    await message.answer(
        text,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    data = read_json("data/summary.json")
    if not data:
        await message.answer("⚠️ Нет data/summary.json")
        return
    text = (
        f"📊 <b>ARGUS</b>\n\n"
        f"📚 Книг: {data.get('total_books', 0)}\n"
        f"📄 Чанков: {data.get('total_chunks', 0)}"
    )
    await message.answer(text, parse_mode="HTML")


@dp.message(Command("ask"))
async def cmd_ask(message: types.Message):
    query = message.text.replace("/ask", "", 1).strip()
    if not query:
        await message.answer(
            "⚠️ Напиши вопрос.\n"
            "Пример: <code>/ask Что такое риск?</code>",
            parse_mode="HTML",
        )
        return
    await message.answer(
        "🔍 <b>Ищу ответ...</b>\n30-60 секунд.",
        parse_mode="HTML",
    )
    send_dispatch("run_search", {
        "query": query,
        "chat_id": str(message.chat.id),
    })


@dp.message(Command("find"))
async def cmd_find(message: types.Message):
    topic = message.text.replace("/find", "", 1).strip()
    if not topic:
        await message.answer(
            "⚠️ Укажи тему.\n"
            "Пример: <code>/find квантовая механика</code>",
            parse_mode="HTML",
        )
        return
    await message.answer(
        f"🔍 Ищу книги: <b>{topic}</b>\n30-60 секунд...",
        parse_mode="HTML",
    )
    send_dispatch("personal_find", {
        "topic": topic,
        "chat_id": str(message.chat.id),
    })


@dp.message(Command("findnext"))
async def cmd_findnext(message: types.Message):
    await message.answer("📖 Загружаю следующие материалы...")
    send_dispatch("personal_next", {})


@dp.message(Command("crypto"))
async def cmd_crypto(message: types.Message):
    await message.answer(
        "🪙 <b>Crypto управление</b>",
        reply_markup=kb_crypto(),
        parse_mode="HTML",
    )


@dp.message(Command("audio"))
async def cmd_audio(message: types.Message):
    await message.answer(
        "🎵 <b>Аудиокниги — скоро</b>\n\n"
        "Функция в разработке.",
        parse_mode="HTML",
    )


@dp.message(Command("train"))
async def cmd_train(message: types.Message):
    await message.answer(
        "⚠️ <b>Train ARGUS</b>\n\n"
        "Обучение займёт 5-30 минут.\n"
        "Запустить?",
        reply_markup=kb_confirm("train"),
        parse_mode="HTML",
    )


@dp.message(Command("collect"))
async def cmd_collect(message: types.Message):
    ok, msg = run_workflow("collect")
    await message.answer(msg)


@dp.message(Command("enrich"))
async def cmd_enrich(message: types.Message):
    ok, msg = run_workflow("enrich")
    await message.answer(msg)


# ============================================================
# CALLBACK — МЕНЮ
# ============================================================
@dp.callback_query(F.data == "menu:main")
async def cb_menu_main(callback: CallbackQuery):
    await callback.message.edit_text(
        "🏛️ <b>Центр управления</b>",
        reply_markup=kb_main(),
        parse_mode="HTML",
    )
    await callback.answer()


@dp.callback_query(F.data == "menu:crypto")
async def cb_menu_crypto(callback: CallbackQuery):
    await callback.message.edit_text(
        "🪙 <b>Crypto управление</b>",
        reply_markup=kb_crypto(),
        parse_mode="HTML",
    )
    await callback.answer()


@dp.callback_query(F.data == "menu:reports")
async def cb_menu_reports(callback: CallbackQuery):
    await callback.message.edit_text(
        "📊 <b>Отчёты</b>",
        reply_markup=kb_reports(),
        parse_mode="HTML",
    )
    await callback.answer()


@dp.callback_query(F.data == "menu:audio")
async def cb_menu_audio(callback: CallbackQuery):
    await callback.message.edit_text(
        "🎵 <b>Аудиокниги — скоро</b>",
        reply_markup=kb_main(),
        parse_mode="HTML",
    )
    await callback.answer()


@dp.callback_query(F.data == "menu:status")
async def cb_menu_status(callback: CallbackQuery):
    await callback.answer("Читаю...")
    text = build_full_status()
    await callback.message.edit_text(
        text,
        reply_markup=kb_main(),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@dp.callback_query(F.data == "menu:train")
async def cb_menu_train(callback: CallbackQuery):
    await callback.message.edit_text(
        "⚠️ <b>Train ARGUS</b>\n\n"
        "Обучение займёт 5-30 минут.\n"
        "Запустить?",
        reply_markup=kb_confirm("train"),
        parse_mode="HTML",
    )
    await callback.answer()


@dp.callback_query(F.data == "menu:help")
async def cb_menu_help(callback: CallbackQuery):
    await callback.answer()
    text = help_text()
    if len(text) > 4000:
        text = text[:3950] + "\n\n<i>...обрезано</i>"
    await callback.message.answer(
        text,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


# ============================================================
# CALLBACK — ДЕЙСТВИЯ
# ============================================================
@dp.callback_query(F.data == "action:collect")
async def cb_action_collect(callback: CallbackQuery):
    await callback.answer("Запускаю...")
    ok, msg = run_workflow("collect")
    await callback.message.edit_text(
        msg + "\n\nРезультат придёт отдельно.",
        reply_markup=kb_crypto(),
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "action:enrich")
async def cb_action_enrich(callback: CallbackQuery):
    await callback.answer("Запускаю...")
    ok, msg = run_workflow("enrich")
    await callback.message.edit_text(
        msg + "\n\nРезультат придёт отдельно.",
        reply_markup=kb_crypto(),
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "action:detect")
async def cb_action_detect(callback: CallbackQuery):
    await callback.answer("Запускаю...")
    ok, msg = run_workflow("detect")
    await callback.message.edit_text(
        msg,
        reply_markup=kb_crypto(),
        parse_mode="HTML",
    )


# ============================================================
# CALLBACK — ПОДТВЕРЖДЕНИЯ
# ============================================================
@dp.callback_query(F.data.startswith("confirm:"))
async def cb_confirm(callback: CallbackQuery):
    key = callback.data.replace("confirm:", "")
    if key == "train":
        await callback.answer("Запускаю Train...")
        ok, msg = run_workflow("train")
        await callback.message.edit_text(
            msg + "\n\nЖди отчёт после завершения.",
            reply_markup=kb_main(),
            parse_mode="HTML",
        )
    else:
        await callback.answer("Неизвестное действие")


# ============================================================
# CALLBACK — ОТЧЁТЫ
# ============================================================
@dp.callback_query(F.data == "report:week")
async def cb_report_week(callback: CallbackQuery):
    await callback.answer("Запускаю...")
    ok, msg = run_workflow("report_week")
    await callback.message.edit_text(
        msg + "\n\nОтчёт с графиками придёт отдельно.",
        reply_markup=kb_reports(),
        parse_mode="HTML",
    )


# ============================================================
# LEGACY CALLBACKS (книги)
# ============================================================
@dp.callback_query(F.data.startswith("approve:"))
async def handle_approve(callback: CallbackQuery):
    sid = callback.data.replace("approve:", "")
    if send_dispatch("approved_download", {"short_id": sid}):
        await callback.message.edit_text(
            callback.message.text + "\n\n✅ <b>Скачиваю...</b>",
            parse_mode="HTML",
        )
        await callback.answer("Отправлено")
    else:
        await callback.answer("Ошибка", show_alert=True)


@dp.callback_query(F.data.startswith("reject:"))
async def handle_reject(callback: CallbackQuery):
    await callback.message.edit_text(
        callback.message.text + "\n\n❌ <b>Отклонено</b>",
        parse_mode="HTML",
    )
    await callback.answer("Отклонено")


@dp.callback_query(F.data.startswith("personal_dl:"))
async def handle_personal_dl(callback: CallbackQuery):
    idx = callback.data.replace("personal_dl:", "")
    if send_dispatch("personal_download", {"index": idx}):
        await callback.message.edit_text(
            callback.message.text + f"\n\n📥 <b>Качаю #{idx}...</b>",
            parse_mode="HTML",
        )
        await callback.answer("Отправлено")
    else:
        await callback.answer("Ошибка", show_alert=True)


@dp.callback_query(F.data.startswith("personal_next:"))
async def handle_personal_next(callback: CallbackQuery):
    if send_dispatch("personal_next", {}):
        await callback.answer("Загружаю...")
    else:
        await callback.answer("Ошибка", show_alert=True)


# ============================================================
# ЗАПУСК
# ============================================================
async def main():
    logger.info("🏛️ ARGUS Bot Host v2.2 запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановлено")