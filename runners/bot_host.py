# ============================================================
# ARGUS - BOT HOST v6.0 (CLEAN & BTC ONLY)
# ------------------------------------------------------------
# Оставлен только сборщик BTC и базовое меню.
# Книги, графики, обучение и старые базы удалены.
# ============================================================

import os
import sys
import asyncio
import logging
import requests

try:
    import psycopg
    PSYCOPG_OK = True
except ImportError:
    PSYCOPG_OK = False

try:
    from aiogram import Bot, Dispatcher, types, F
    from aiogram.filters import Command
    from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
except ImportError as e:
    print("ERR: aiogram not installed: " + str(e))
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("WARN: python-dotenv not installed")

BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
ADMIN_CHAT_ID = (os.getenv("ADMIN_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID") or "").strip()

if not BOT_TOKEN:
    print("ERR: BOT_TOKEN not set")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)
logger.info("BOT_TOKEN: " + str(len(BOT_TOKEN)))
logger.info("PSYCOPG_OK: " + str(PSYCOPG_OK))
logger.info("ADMIN_CHAT_ID: " + (ADMIN_CHAT_ID or "NOT SET"))

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BINANCE_DIR = os.path.join(SCRIPT_DIR, "binance")
if BINANCE_DIR not in sys.path:
    sys.path.insert(0, BINANCE_DIR)

try:
    import btc_data_binance
    BTC_COLLECT_OK = True
    logger.info("BTC_COLLECT: ok")
except Exception as e:
    BTC_COLLECT_OK = False
    logger.warning("btc_data_binance: " + str(e))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ============================================================
# МЕНЮ И КЛАВИАТУРЫ
# ============================================================
def kb_main():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🪙 Крипто", callback_data="menu:crypto"),
            InlineKeyboardButton(text="🤖 Спросить", callback_data="menu:ask"),
        ],
        [
            InlineKeyboardButton(text="📊 Статус", callback_data="menu:status"),
        ],
    ])

def kb_back(target="menu:main"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Назад", callback_data=target)],
    ])

def kb_crypto():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="BTC ON", callback_data="btc:on"),
            InlineKeyboardButton(text="BTC OFF", callback_data="btc:off"),
        ],
        [
            InlineKeyboardButton(text="BTC статус", callback_data="btc:status"),
        ],
        [
            InlineKeyboardButton(text="Назад", callback_data="menu:main"),
        ],
    ])

def short_info():
    return (
        "🤖 <b>ARGUS System</b>\n\n"
        "Система сбора данных и сигналов.\n"
        "• Сбор BTC (15m)\n"
        "• Сигналы (LONG/SHORT)\n\n"
        "Выбери действие:"
    )

def btc_status_report():
    if not BTC_COLLECT_OK:
        return "❌ btc_data_binance not loaded"
    state = "🟢 ON" if btc_data_binance.is_enabled() else "🔴 OFF"
    lines = ["🪙 <b>BTC 15m collector</b>"]
    lines.append("")
    lines.append(f"Состояние: {state}")
    lines.append(f"Флаг: {btc_data_binance.FLAG_FILE}")
    lines.append(f"Символ: {btc_data_binance.SYMBOL}")
    lines.append(f"Таймфрейм: {btc_data_binance.TIMEFRAME}")
    return "\n".join(lines)

def build_status():
    lines = ["📊 <b>ARGUS Status</b>\n"]
    lines.append("Сборщик: " + ("🟢 Работает" if BTC_COLLECT_OK else "🔴 Остановлен"))
    lines.append("psycopg: " + ("🟢 OK" if PSYCOPG_OK else "🔴 Ошибка"))
    if BTC_COLLECT_OK:
        state = "🟢 ON" if btc_data_binance.is_enabled() else "🔴 OFF"
        lines.append(f"BTC сбор: {state}")
    return "\n".join(lines)

# ============================================================
# ХЕНДЛЕРЫ КОМАНД
# ============================================================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(short_info(), reply_markup=kb_main(), parse_mode="HTML")

@dp.message(Command("panel"))
async def cmd_panel(message: types.Message):
    await message.answer("Центр управления", reply_markup=kb_main())

@dp.message(Command("ask"))
async def cmd_ask(message: types.Message):
    q = message.text.replace("/ask", "", 1).strip()
    if not q:
        await message.answer("Напиши: /ask твой вопрос")
        return
    # Пока заглушка для будущего ИИ
    await message.answer(f"🤖 <i>Принял вопрос:</i> {q}\n\n<i>(Интеграция с ИИ будет добавлена позже)</i>", parse_mode="HTML")

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    await message.answer(build_status(), parse_mode="HTML")

@dp.message(Command("crypto"))
async def cmd_crypto(message: types.Message):
    await message.answer("Управление сбором", reply_markup=kb_crypto())

@dp.message(Command("btc_on"))
async def cmd_btc_on(message: types.Message):
    if not BTC_COLLECT_OK:
        await message.answer("btc_data_binance not loaded")
        return
    btc_data_binance.set_enabled(True)
    await message.answer("🟢 BTC 15m collector: ON")

@dp.message(Command("btc_off"))
async def cmd_btc_off(message: types.Message):
    if not BTC_COLLECT_OK:
        await message.answer("btc_data_binance not loaded")
        return
    btc_data_binance.set_enabled(False)
    await message.answer("🔴 BTC 15m collector: OFF")

@dp.message(Command("btc_status"))
async def cmd_btc_status(message: types.Message):
    if not BTC_COLLECT_OK:
        await message.answer("btc_data_binance not loaded")
        return
    await message.answer(btc_status_report(), parse_mode="HTML")

# ============================================================
# ХЕНДЛЕРЫ КНОПОК
# ============================================================
@dp.callback_query(F.data == "menu:main")
async def cb_main(cb: CallbackQuery):
    await cb.message.edit_text(short_info(), reply_markup=kb_main(), parse_mode="HTML")
    await cb.answer()

@dp.callback_query(F.data == "menu:crypto")
async def cb_crypto(cb: CallbackQuery):
    await cb.message.edit_text("🪙 Управление сбором данных:", reply_markup=kb_crypto())
    await cb.answer()

@dp.callback_query(F.data == "menu:ask")
async def cb_ask(cb: CallbackQuery):
    await cb.message.edit_text(
        "🤖 <b>Спросить ARGUS</b>\n\nНапиши в чат:\n<code>/ask твой вопрос</code>",
        reply_markup=kb_back(),
        parse_mode="HTML"
    )
    await cb.answer()

@dp.callback_query(F.data == "menu:status")
async def cb_status(cb: CallbackQuery):
    await cb.answer("Читаю...")
    await cb.message.edit_text(
        build_status(), reply_markup=kb_back(), parse_mode="HTML"
    )

@dp.callback_query(F.data == "btc:on")
async def cb_btc_on(cb: CallbackQuery):
    if not BTC_COLLECT_OK:
        await cb.answer("Модуль не загружен", show_alert=True)
        return
    btc_data_binance.set_enabled(True)
    await cb.answer("BTC 15m collector ON")

@dp.callback_query(F.data == "btc:off")
async def cb_btc_off(cb: CallbackQuery):
    if not BTC_COLLECT_OK:
        await cb.answer("Модуль не загружен", show_alert=True)
        return
    btc_data_binance.set_enabled(False)
    await cb.answer("BTC 15m collector OFF")

@dp.callback_query(F.data == "btc:status")
async def cb_btc_status(cb: CallbackQuery):
    await cb.answer("Читаю...")
    await cb.message.edit_text(
        btc_status_report(), reply_markup=kb_crypto(), parse_mode="HTML"
    )

# ============================================================
# ФОНОВЫЙ СБОР ДАННЫХ (НЕ ТРОНУТ!)
# ============================================================
async def btc_loop():
    if not BTC_COLLECT_OK:
        logger.warning("btc_loop: btc_data_binance not loaded")
        return
    if not PSYCOPG_OK:
        logger.warning("btc_loop: psycopg not installed")
        return

    logger.info("btc_loop: started")
    loop = asyncio.get_running_loop()
    await asyncio.sleep(180) # Ждем 3 минуты при старте

    while True:
        try:
            if btc_data_binance.is_enabled():
                logger.info("btc_loop: tick")
                added, err = await loop.run_in_executor(
                    None, btc_data_binance.collect_btc,
                )
                logger.info("btc_loop: added=%d err=%s", added, err)
                if err and ADMIN_CHAT_ID:
                    try:
                        await bot.send_message(
                            chat_id=ADMIN_CHAT_ID,
                            text="⚠️ BTC collector error\n\n" + str(err)[:3500],
                        )
                    except Exception as e:
                        logger.error("notify err: %s", e)
            else:
                logger.info("btc_loop: disabled, skip")
        except Exception as e:
            logger.error("btc_loop error: %s", e)
            if ADMIN_CHAT_ID:
                try:
                    await bot.send_message(
                        chat_id=ADMIN_CHAT_ID,
                        text="⚠️ btc_loop crash\n\n" + str(e)[:3500],
                    )
                except Exception:
                    pass
        await asyncio.sleep(900) # 15 минут

async def main():
    logger.info("ARGUS Bot Host v6.0 started")
    asyncio.create_task(btc_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped")