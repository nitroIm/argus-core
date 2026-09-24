# ============================================================
# ARGUS - BOT HOST v3.5
# ------------------------------------------------------------
# v3.5: fix approve -> approved_download идёт в argus-core.
#       (было "books", стало "core")
# v3.4: два репозитория.
#       GITHUB_REPO       -> argus-core
#       GITHUB_REPO_BOOKS -> personal-books
#       personal_* идут в books.
# ============================================================

import os
import sys
import base64
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
    print("ERR: aiogram not installed: " + str(e))
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("WARN: python-dotenv not installed")


BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
GITHUB_PAT = (
    os.getenv("GH_PAT")
    or os.getenv("GITHUB_PAT")
    or ""
).strip()
GITHUB_REPO = (os.getenv("GITHUB_REPO") or "").strip()
GITHUB_REPO_BOOKS = (
    os.getenv("GITHUB_REPO_BOOKS") or ""
).strip()

if not BOT_TOKEN:
    print("ERR: BOT_TOKEN not set")
    sys.exit(1)
if not GITHUB_REPO:
    print("ERR: GITHUB_REPO not set")
    sys.exit(1)
if not GITHUB_REPO_BOOKS:
    print("WARN: GITHUB_REPO_BOOKS not set")
    print("      personal-* will use GITHUB_REPO")


def repo_of(kind):
    """core -> argus-core, books -> personal-books."""
    if kind == "books":
        return GITHUB_REPO_BOOKS or GITHUB_REPO
    return GITHUB_REPO


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)
logger.info("BOT_TOKEN: " + str(len(BOT_TOKEN)))
logger.info("REPO: " + GITHUB_REPO)
logger.info("REPO_BOOKS: " + (GITHUB_REPO_BOOKS or "-"))


WORKFLOWS = {
    "train": {
        "file": "train_model.yml",
        "repo": "core",
    },
    "collect": {
        "file": "crypto_collect.yml",
        "repo": "core",
    },
    "enrich": {
        "file": "crypto_enrich.yml",
        "repo": "core",
    },
    "detect": {
        "file": "crypto_detect.yml",
        "repo": "core",
    },
    "report_week": {
        "file": "crypto_reporter.yml",
        "repo": "core",
    },
    "audio": {
        "file": "personal_audio.yml",
        "repo": "books",
    },
}


# ------------------------------------------------------------
# GITHUB API
# ------------------------------------------------------------
def gh_headers(accept="application/vnd.github+json"):
    return {
        "Accept": accept,
        "Authorization": "Bearer " + GITHUB_PAT,
        "X-GitHub-Api-Version": "2022-11-28",
    }


def run_workflow(key, inputs=None):
    wf = WORKFLOWS.get(key)
    if not wf:
        return False, "Unknown"
    repo = repo_of(wf.get("repo", "core"))
    url = "https://api.github.com/repos/"
    url += repo
    url += "/actions/workflows/"
    url += wf["file"]
    url += "/dispatches"
    data = {"ref": "main"}
    if inputs:
        data["inputs"] = inputs
    try:
        r = requests.post(
            url, headers=gh_headers(),
            json=data, timeout=15,
        )
        if r.status_code in (200, 201, 204):
            return True, "OK"
        if r.status_code == 404:
            return False, "not found"
        if r.status_code == 403:
            return False, "no scope"
        return False, "HTTP " + str(r.status_code)
    except Exception as e:
        return False, str(e)


def read_json(path, repo_kind="core"):
    repo = repo_of(repo_kind)
    url = "https://api.github.com/repos/"
    url += repo
    url += "/contents/"
    url += path
    headers = gh_headers("application/vnd.github.raw")
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {}


def read_binary(path, repo_kind="core"):
    repo = repo_of(repo_kind)
    url = "https://api.github.com/repos/"
    url += repo
    url += "/contents/"
    url += path
    try:
        r = requests.get(
            url, headers=gh_headers(), timeout=20,
        )
        if r.status_code == 200:
            data = r.json()
            content = data.get("content", "")
            if content:
                return base64.b64decode(content)
    except Exception:
        pass
    return None


def list_dir(path, repo_kind="core"):
    repo = repo_of(repo_kind)
    url = "https://api.github.com/repos/"
    url += repo
    url += "/contents/"
    url += path
    try:
        r = requests.get(
            url, headers=gh_headers(), timeout=15,
        )
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return []


def delete_file(path, sha, message="delete",
                repo_kind="core"):
    repo = repo_of(repo_kind)
    url = "https://api.github.com/repos/"
    url += repo
    url += "/contents/"
    url += path
    data = {
        "message": message,
        "sha": sha,
        "branch": "main",
    }
    try:
        r = requests.delete(
            url, headers=gh_headers(),
            json=data, timeout=15,
        )
        return r.status_code in (200, 204)
    except Exception:
        return False


def send_dispatch(event_type, payload,
                  repo_kind="core"):
    repo = repo_of(repo_kind)
    url = "https://api.github.com/repos/"
    url += repo
    url += "/dispatches"
    data = {
        "event_type": event_type,
        "client_payload": payload,
    }
    try:
        r = requests.post(
            url, headers=gh_headers(),
            json=data, timeout=15,
        )
        return r.status_code == 204
    except Exception:
        return False


# ------------------------------------------------------------
# HELPERS
# ------------------------------------------------------------
def fmt_price(p):
    if p >= 1000:
        return "$" + format(int(p), ",")
    if p >= 1:
        return "$" + format(p, ".2f")
    return "$" + format(p, ".4f")


def personal_books():
    items = list_dir("personal_books", "books")
    books = {}
    for it in items:
        n = it.get("name", "")
        if not n.endswith(".pdf"):
            continue
        stem = n[:-4]
        if stem.endswith("_RU"):
            base = stem[:-3]
            books.setdefault(base, {})["ru"] = n
        else:
            books.setdefault(stem, {})["en"] = n
    return books


def books_in_queue():
    items = list_dir("books", "core")
    result = []
    for it in items:
        n = it.get("name", "")
        if n.endswith(".pdf") or n.endswith(".txt"):
            result.append(n)
    return result


def trained_books():
    s = read_json("data/summary.json", "core")
    if not s:
        return []
    books = s.get("books", [])
    result = []
    for b in books:
        result.append({
            "file": b.get("file", "?"),
            "chunks": b.get("chunks", 0),
        })
    return result


def build_status():
    lines = ["📊 ARGUS - статус", ""]
    s = read_json("data/summary.json", "core")
    if s:
        lines.append("🏛️ ARGUS")
        lines.append("📚 Книг: " + str(s.get("total_books", 0)))
        lines.append(
            "📄 Чанков: " + str(s.get("total_chunks", 0))
        )
    else:
        lines.append("🏛️ ARGUS: нет данных")
    lines.append("")
    levels = read_json(
        "crypto/data/levels_analysis.json", "core",
    )
    patterns = read_json(
        "crypto/data/patterns_analysis.json", "core",
    )
    if levels or patterns:
        lines.append("🪙 Crypto")
        if levels and levels.get("symbols"):
            for sym, d in levels["symbols"].items():
                name = sym.replace("USDT", "")
                price = d.get("current_price", 0)
                lines.append(
                    "💰 " + name + ": "
                    + fmt_price(price)
                )
                sup = d.get("supports", [])
                if sup:
                    s1 = sup[0]
                    lines.append(
                        "  support "
                        + fmt_price(s1["price"])
                    )
                res = d.get("resistances", [])
                if res:
                    r1 = res[0]
                    lines.append(
                        "  resist "
                        + fmt_price(r1["price"])
                    )
        if patterns and patterns.get("symbols"):
            lines.append("")
            lines.append("🧩 Patterns")
            for sym, d in patterns["symbols"].items():
                name = sym.replace("USDT", "")
                up = d.get("up_ratio", 0) * 100
                mk = d.get("markov", {})
                p10 = mk.get("p_1_given_0", 0)
                line = "  " + name + ": "
                line += format(up, ".0f") + "% up"
                line += "  P(1|0)="
                line += format(p10, ".2f")
                lines.append(line)
    else:
        lines.append("🪙 Crypto: нет данных")
    return "\n".join(lines)


def short_info():
    return (
        "🏛️ ARGUS\n"
        "Автономная система знаний и анализа.\n\n"
        "Что умеет:\n"
        "📚 Хранить знания из книг\n"
        "🔍 Отвечать на вопросы (/ask)\n"
        "📥 Искать и скачивать книги (/find)\n"
        "🪙 Собирать данные BTC/ETH\n"
        "📈 Строить паттерны и прогнозы\n"
        "🎧 Озвучивать книги\n\n"
        "Выбирай кнопки внизу."
    )


# ------------------------------------------------------------
# BOT
# ------------------------------------------------------------
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def kb_main():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📚 Мои книги",
                callback_data="menu:books",
            ),
            InlineKeyboardButton(
                text="🎓 Train",
                callback_data="menu:train",
            ),
        ],
        [
            InlineKeyboardButton(
                text="💬 Спросить",
                callback_data="menu:ask",
            ),
            InlineKeyboardButton(
                text="🪙 Крипто",
                callback_data="menu:crypto",
            ),
        ],
        [
            InlineKeyboardButton(
                text="📊 Графики",
                callback_data="charts:menu",
            ),
            InlineKeyboardButton(
                text="📈 Статус",
                callback_data="menu:status",
            ),
        ],
        [
            InlineKeyboardButton(
                text="⚙️ Настройки",
                callback_data="menu:settings",
            ),
        ],
    ])


def kb_back(target="menu:main"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="◀️ Назад",
                callback_data=target,
            ),
        ],
    ])


def kb_crypto():
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
            InlineKeyboardButton(
                text="📊 Отчёт недели",
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


def kb_train():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📥 Очередь",
                callback_data="train:queue",
            ),
            InlineKeyboardButton(
                text="✅ Обучено",
                callback_data="train:trained",
            ),
        ],
        [
            InlineKeyboardButton(
                text="🚀 Запустить Train",
                callback_data="train:start",
            ),
        ],
        [
            InlineKeyboardButton(
                text="◀️ Назад",
                callback_data="menu:main",
            ),
        ],
    ])


def kb_settings():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🔔 Уведомления (скоро)",
                callback_data="settings:soon",
            ),
        ],
        [
            InlineKeyboardButton(
                text="🌐 Язык (скоро)",
                callback_data="settings:soon",
            ),
        ],
        [
            InlineKeyboardButton(
                text="◀️ Назад",
                callback_data="menu:main",
            ),
        ],
    ])


def kb_charts():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🪙 BTC свечи",
                callback_data="charts:show:btc_candles",
            ),
            InlineKeyboardButton(
                text="🪙 ETH свечи",
                callback_data="charts:show:eth_candles",
            ),
        ],
        [
            InlineKeyboardButton(
                text="🧩 BTC паттерн",
                callback_data="charts:show:btc_pattern",
            ),
            InlineKeyboardButton(
                text="🧩 ETH паттерн",
                callback_data="charts:show:eth_pattern",
            ),
        ],
        [
            InlineKeyboardButton(
                text="🧠 BTC Markov",
                callback_data="charts:show:btc_markov",
            ),
            InlineKeyboardButton(
                text="🧠 ETH Markov",
                callback_data="charts:show:eth_markov",
            ),
        ],
        [
            InlineKeyboardButton(
                text="◀️ Назад",
                callback_data="menu:main",
            ),
        ],
    ])


def kb_books_list(books):
    rows = []
    for name in books[:10]:
        rows.append([
            InlineKeyboardButton(
                text="📖 " + name[:35],
                callback_data="book:view:" + name,
            ),
        ])
    rows.append([
        InlineKeyboardButton(
            text="🔄 Обновить",
            callback_data="menu:books",
        ),
    ])
    rows.append([
        InlineKeyboardButton(
            text="◀️ Назад",
            callback_data="menu:main",
        ),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_book_actions(name):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🎧 Аудио",
                callback_data="book:audio:" + name,
            ),
            InlineKeyboardButton(
                text="🗑 Удалить",
                callback_data="book:del:" + name,
            ),
        ],
        [
            InlineKeyboardButton(
                text="◀️ К списку",
                callback_data="menu:books",
            ),
        ],
    ])


def kb_confirm(action, payload=""):
    cb_yes = "confirm:" + action
    if payload:
        cb_yes += ":" + payload
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Да",
                callback_data=cb_yes,
            ),
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data="menu:main",
            ),
        ],
    ])


# ------------------------------------------------------------
# COMMANDS
# ------------------------------------------------------------
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        short_info(), reply_markup=kb_main(),
    )


@dp.message(Command("panel"))
async def cmd_panel(message: types.Message):
    await message.answer(
        "🏛️ Центр управления",
        reply_markup=kb_main(),
    )


@dp.message(Command("ask"))
async def cmd_ask(message: types.Message):
    q = message.text.replace("/ask", "", 1).strip()
    if not q:
        await message.answer("Напиши: /ask вопрос")
        return
    await message.answer("🔍 Ищу ответ... 30-60 сек")
    send_dispatch("run_search", {
        "query": q,
        "chat_id": str(message.chat.id),
    }, "core")


@dp.message(Command("find"))
async def cmd_find(message: types.Message):
    t = message.text.replace("/find", "", 1).strip()
    if not t:
        await message.answer("Напиши: /find тема")
        return
    await message.answer("🔍 Ищу книги: " + t)
    send_dispatch("personal_find", {
        "topic": t,
        "chat_id": str(message.chat.id),
    }, "books")


@dp.message(Command("findnext"))
async def cmd_findnext(message: types.Message):
    await message.answer("📖 Загружаю...")
    send_dispatch("personal_next", {}, "books")


@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    await message.answer("⏳ Читаю...")
    t = build_status()
    await message.answer(
        t, disable_web_page_preview=True,
    )


@dp.message(Command("crypto"))
async def cmd_crypto(message: types.Message):
    await message.answer(
        "🪙 Crypto", reply_markup=kb_crypto(),
    )


@dp.message(Command("train"))
async def cmd_train(message: types.Message):
    await message.answer(
        "🎓 Train ARGUS", reply_markup=kb_train(),
    )


@dp.message(Command("charts"))
async def cmd_charts(message: types.Message):
    await message.answer(
        "📊 Графики", reply_markup=kb_charts(),
    )


# ------------------------------------------------------------
# CALLBACKS - MENU
# ------------------------------------------------------------
@dp.callback_query(F.data == "menu:main")
async def cb_main(cb: CallbackQuery):
    await cb.message.edit_text(
        short_info(), reply_markup=kb_main(),
    )
    await cb.answer()


@dp.callback_query(F.data == "menu:books")
async def cb_books(cb: CallbackQuery):
    await cb.answer()
    books = personal_books()
    if not books:
        await cb.message.edit_text(
            "📚 Мои книги\n\n"
            "Пока пусто.\n"
            "Найди через /find.",
            reply_markup=kb_back(),
        )
        return
    names = sorted(books.keys())
    await cb.message.edit_text(
        "📚 Мои книги (" + str(len(names)) + "):",
        reply_markup=kb_books_list(names),
    )


@dp.callback_query(F.data.startswith("book:view:"))
async def cb_book_view(cb: CallbackQuery):
    name = cb.data.replace("book:view:", "", 1)
    books = personal_books()
    if name not in books:
        await cb.answer("Не найдено", show_alert=True)
        return
    variants = books[name]
    flags = []
    if "ru" in variants:
        flags.append("RU")
    if "en" in variants:
        flags.append("EN")
    text = "📖 " + name + "\n"
    text += "Форматы: " + " + ".join(flags)
    await cb.message.edit_text(
        text, reply_markup=kb_book_actions(name),
    )
    await cb.answer()


@dp.callback_query(F.data.startswith("book:audio:"))
async def cb_book_audio(cb: CallbackQuery):
    name = cb.data.replace("book:audio:", "", 1)
    await cb.answer("Запускаю...")
    ok, msg = run_workflow("audio", {
        "book": name,
        "prefer": "ru",
    })
    if ok:
        await cb.message.edit_text(
            "🎧 Обрабатываю\n" + name
            + "\n\nmp3 придёт автоматически.",
            reply_markup=kb_book_actions(name),
        )
    else:
        await cb.message.edit_text(
            "❌ " + msg,
            reply_markup=kb_book_actions(name),
        )


@dp.callback_query(F.data.startswith("book:del:"))
async def cb_book_del(cb: CallbackQuery):
    name = cb.data.replace("book:del:", "", 1)
    await cb.message.edit_text(
        "🗑 Удалить книгу?\n\n" + name,
        reply_markup=kb_confirm("book_del", name),
    )
    await cb.answer()


@dp.callback_query(
    F.data.startswith("confirm:book_del:")
)
async def cb_confirm_book_del(cb: CallbackQuery):
    name = cb.data.replace(
        "confirm:book_del:", "", 1,
    )
    items = list_dir("personal_books", "books")
    deleted = 0
    for it in items:
        fname = it.get("name", "")
        if not fname.endswith(".pdf"):
            continue
        stem = fname[:-4]
        base = stem[:-3] if stem.endswith("_RU") else stem
        if base != name:
            continue
        ok = delete_file(
            "personal_books/" + fname,
            it.get("sha", ""),
            message="delete: " + name,
            repo_kind="books",
        )
        if ok:
            deleted += 1
    await cb.message.edit_text(
        "✅ Удалено файлов: " + str(deleted),
        reply_markup=kb_back("menu:books"),
    )
    await cb.answer()


@dp.callback_query(F.data == "menu:train")
async def cb_train(cb: CallbackQuery):
    await cb.message.edit_text(
        "🎓 Train ARGUS", reply_markup=kb_train(),
    )
    await cb.answer()


@dp.callback_query(F.data == "train:queue")
async def cb_train_queue(cb: CallbackQuery):
    await cb.answer()
    queue = books_in_queue()
    if not queue:
        text = "📥 Очередь\n\nПусто."
    else:
        text = "📥 В очереди: " + str(len(queue))
        text += "\n\n"
        for i, f in enumerate(queue[:20], 1):
            text += str(i) + ". " + f + "\n"
    await cb.message.edit_text(
        text, reply_markup=kb_train(),
    )


@dp.callback_query(F.data == "train:trained")
async def cb_train_trained(cb: CallbackQuery):
    await cb.answer()
    trained = trained_books()
    if not trained:
        text = "✅ Обучено\n\nПусто."
    else:
        total = len(trained)
        text = "✅ Обучено: " + str(total) + "\n\n"
        for i, b in enumerate(trained[:20], 1):
            text += str(i) + ". " + b["file"]
            text += " (" + str(b["chunks"]) + ")\n"
        if total > 20:
            text += "...и ещё " + str(total - 20)
    if len(text) > 4000:
        text = text[:3950] + "\n..."
    await cb.message.edit_text(
        text, reply_markup=kb_train(),
    )


@dp.callback_query(F.data == "train:start")
async def cb_train_start(cb: CallbackQuery):
    await cb.message.edit_text(
        "⚠️ Запустить Train?\n\n5-30 минут.",
        reply_markup=kb_confirm("train"),
    )
    await cb.answer()


@dp.callback_query(F.data == "confirm:train")
async def cb_confirm_train(cb: CallbackQuery):
    await cb.answer("Запускаю...")
    ok, msg = run_workflow("train")
    if ok:
        await cb.message.edit_text(
            "✅ Train запущен",
            reply_markup=kb_main(),
        )
    else:
        await cb.message.edit_text(
            "❌ " + msg, reply_markup=kb_main(),
        )


@dp.callback_query(F.data == "menu:ask")
async def cb_ask(cb: CallbackQuery):
    await cb.message.edit_text(
        "💬 Спросить ARGUS\n\n"
        "Напиши:\n/ask твой вопрос",
        reply_markup=kb_back(),
    )
    await cb.answer()


@dp.callback_query(F.data == "menu:crypto")
async def cb_crypto(cb: CallbackQuery):
    await cb.message.edit_text(
        "🪙 Crypto", reply_markup=kb_crypto(),
    )
    await cb.answer()


@dp.callback_query(F.data == "menu:settings")
async def cb_settings(cb: CallbackQuery):
    await cb.message.edit_text(
        "⚙️ Настройки\n\nВ разработке.",
        reply_markup=kb_settings(),
    )
    await cb.answer()


@dp.callback_query(F.data == "settings:soon")
async def cb_settings_soon(cb: CallbackQuery):
    await cb.answer("Скоро", show_alert=True)


@dp.callback_query(F.data == "menu:status")
async def cb_status(cb: CallbackQuery):
    await cb.answer("Читаю...")
    t = build_status()
    await cb.message.edit_text(
        t,
        reply_markup=kb_back(),
        disable_web_page_preview=True,
    )


# ------------------------------------------------------------
# CHARTS
# ------------------------------------------------------------
@dp.callback_query(F.data == "charts:menu")
async def cb_charts_menu(cb: CallbackQuery):
    await cb.message.edit_text(
        "📊 Графики", reply_markup=kb_charts(),
    )
    await cb.answer()


@dp.callback_query(F.data.startswith("charts:show:"))
async def cb_charts_show(cb: CallbackQuery):
    name = cb.data.replace("charts:show:", "", 1)
    await cb.answer("Загружаю...")
    path = "data/charts/" + name + ".png"
    photo = read_binary(path, "core")
    if not photo:
        await cb.message.answer("⚠️ График не найден")
        return
    try:
        url = "https://api.telegram.org/bot"
        url += BOT_TOKEN + "/sendPhoto"
        files = {"photo": (name + ".png", photo)}
        data = {"chat_id": str(cb.from_user.id)}
        requests.post(
            url, data=data, files=files, timeout=30,
        )
    except Exception:
        pass


# ------------------------------------------------------------
# CRYPTO
# ------------------------------------------------------------
@dp.callback_query(F.data == "action:collect")
async def cb_collect(cb: CallbackQuery):
    await cb.answer("Запускаю...")
    ok, msg = run_workflow("collect")
    await cb.message.edit_text(
        "✅ Collect" if ok else "❌ " + msg,
        reply_markup=kb_crypto(),
    )


@dp.callback_query(F.data == "action:enrich")
async def cb_enrich(cb: CallbackQuery):
    await cb.answer("Запускаю...")
    ok, msg = run_workflow("enrich")
    await cb.message.edit_text(
        "✅ Enrich" if ok else "❌ " + msg,
        reply_markup=kb_crypto(),
    )


@dp.callback_query(F.data == "action:detect")
async def cb_detect(cb: CallbackQuery):
    await cb.answer("Запускаю...")
    ok, msg = run_workflow("detect")
    await cb.message.edit_text(
        "✅ Detect" if ok else "❌ " + msg,
        reply_markup=kb_crypto(),
    )


@dp.callback_query(F.data == "report:week")
async def cb_report(cb: CallbackQuery):
    await cb.answer("Запускаю...")
    ok, msg = run_workflow("report_week")
    await cb.message.edit_text(
        "✅ Отчёт запущен" if ok else "❌ " + msg,
        reply_markup=kb_crypto(),
    )


# ------------------------------------------------------------
# PERSONAL DOWNLOAD -> dispatch (books)
# ------------------------------------------------------------
@dp.callback_query(F.data.startswith("personal_dl:"))
async def cb_pdl(cb: CallbackQuery):
    idx = cb.data.replace("personal_dl:", "")
    await cb.answer("Запускаю...")

    cand = read_json(
        "data/personal_candidates.json", "books",
    )
    if not cand:
        await cb.message.edit_text("❌ Нет списка")
        return

    items = cand.get("items", [])
    try:
        i = int(idx)
    except ValueError:
        await cb.answer("Ошибка", show_alert=True)
        return

    if i < 0 or i >= len(items):
        await cb.answer(
            "Вне диапазона", show_alert=True,
        )
        return

    item = items[i]
    url = item.get("url")
    title = item.get("title", "book")

    if not url:
        await cb.message.edit_text("❌ Нет URL")
        return

    send_dispatch("personal_download", {
        "url": url,
        "title": title,
    }, "books")

    await cb.message.edit_text(
        "📥 Скачиваю\n\n"
        + title[:150]
        + "\n\n⏳ 1-2 минуты.\n"
        + "PDF появится в personal_books/"
    )


@dp.callback_query(
    F.data.startswith("personal_reject:")
)
async def cb_personal_reject(cb: CallbackQuery):
    await cb.answer("Отклонено")
    try:
        await cb.message.edit_reply_markup(
            reply_markup=None,
        )
    except Exception:
        pass


@dp.callback_query(
    F.data.startswith("personal_next:")
)
async def cb_pnext(cb: CallbackQuery):
    await cb.answer("Загружаю...")

    cand = read_json(
        "data/personal_candidates.json", "books",
    )
    if not cand:
        return

    items = cand.get("items", [])
    offset = cand.get("offset", 5)
    total = len(items)

    next_batch = items[offset:offset + 5]
    if not next_batch:
        await cb.message.edit_text("Больше нет книг")
        return

    for i, item in enumerate(next_batch):
        real_i = offset + i
        title = item.get("title", "?")[:200]
        source = item.get("source", "?")
        card = "<b>" + str(real_i + 1) + "/"
        card += str(total) + "</b>\n"
        card += "<b>" + title + "</b>\n\n"
        card += "📡 " + source
        kb = {
            "inline_keyboard": [[
                {
                    "text": "✅ Скачать",
                    "callback_data": "personal_dl:"
                                     + str(real_i),
                },
                {
                    "text": "❌ Отклонить",
                    "callback_data":
                        "personal_reject:"
                        + str(real_i),
                },
            ]],
        }
        try:
            url = "https://api.telegram.org/bot"
            url += BOT_TOKEN + "/sendMessage"
            requests.post(url, json={
                "chat_id": cb.from_user.id,
                "text": card,
                "parse_mode": "HTML",
                "reply_markup": kb,
                "disable_web_page_preview": True,
            }, timeout=15)
        except Exception:
            pass

    try:
        await cb.message.edit_reply_markup(
            reply_markup=None,
        )
    except Exception:
        pass


# ------------------------------------------------------------
# APPROVE (ARGUS Core -> argus-core)
# ------------------------------------------------------------
@dp.callback_query(F.data.startswith("approve:"))
async def cb_approve(cb: CallbackQuery):
    sid = cb.data.replace("approve:", "")
    ok = send_dispatch(
        "approved_download", {"short_id": sid},
        "core",
    )
    if ok:
        await cb.message.edit_text(
            cb.message.text + "\n\n✅ Скачиваю...",
        )
        await cb.answer("OK")
    else:
        await cb.answer("Ошибка", show_alert=True)


@dp.callback_query(F.data.startswith("reject:"))
async def cb_reject(cb: CallbackQuery):
    await cb.message.edit_text(
        cb.message.text + "\n\n❌ Отклонено",
    )
    await cb.answer("OK")


# ------------------------------------------------------------
# RUN
# ------------------------------------------------------------
async def main():
    logger.info("ARGUS Bot Host v3.5 started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped")