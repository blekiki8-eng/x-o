import os
import uuid
import logging
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

# Налаштування
API_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")
EXCHANGE_RATE = 44.5  # Курс 1$ (1💎) = 44.5₴
BONUS_PERCENT = 0.05  # Бонус +5%

logging.basicConfig(level=logging.INFO)
storage = MemoryStorage()
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=storage)

cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["fishcash_game"]
users_col = db["users"]

# Класи станів для очікування суми
class DepositState(StatesGroup):
    wait_amount = State()

# --- Клавіатури ---

def main_menu():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton("👤 Профіль"), KeyboardButton("🎮 Ігри"))
    keyboard.add(KeyboardButton("💎 Баланс"))
    return keyboard

def balance_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("💳 Поповнення", callback_data="deposit"),
        InlineKeyboardButton("📤 Виведення", callback_data="withdraw")
    )
    return markup

# --- Функції БД ---

async def get_user(user_id, username):
    user = await users_col.find_one({"_id": user_id})
    if not user:
        user = {"_id": user_id, "username": username, "balance": 0}
        await users_col.insert_one(user)
    return user

# --- Обробники ---

@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    await get_user(message.from_user.id, message.from_user.username or "Гравець")
    await message.answer("Вітаємо у FishCash Game! 💎", reply_markup=main_menu())

@dp.message_handler(lambda message: message.text == "💎 Баланс")
async def show_balance(message: types.Message):
    user = await get_user(message.from_user.id, message.from_user.username)
    await message.answer(
        f"💎 **Ваш баланс: {user['balance']} 💎**\n"
        f"Курс: 1 💎 = {EXCHANGE_RATE}₴",
        reply_markup=balance_keyboard(),
        parse_mode="Markdown"
    )

@dp.callback_query_handler(lambda c: c.data == "deposit")
async def deposit_start(callback: types.CallbackQuery):
    await bot.send_message(callback.from_user.id, "💰 Введіть суму в 💎 (доларів), яку хочете поповнити:")
    await DepositState.wait_amount.set()
    await callback.answer()

@dp.message_handler(state=DepositState.wait_amount)
async def process_deposit(message: types.Message, state: FSMContext):
    try:
        amount_diamonds = float(message.text)
        if amount_diamonds <= 0:
            return await message.answer("❌ Сума має бути більшою за 0.")
        
        # Розрахунок: Сума в грн + 5%
        amount_uah = amount_diamonds * EXCHANGE_RATE
        bonus = amount_uah * BONUS_PERCENT
        total_uah = amount_uah + bonus

        response = (
            f"📑 **Заявка: {amount_diamonds} 💎**\n"
            f"📈 До поповнення балансу (+5%): {total_uah:.2f}₴\n"
            f"💵 **Сума поповнення: {total_uah:.2f}₴**\n\n"
            f"💳 Реквізити для оплати:\n"
            f"`5355 2800 2890 2177`\n\n"
            f"⚠️ **Обов’язково сюди кидайте квитанцію для підтвердження!**"
        )
        
        await message.answer(response, parse_mode="Markdown")
        await state.finish()
        
    except ValueError:
        await message.answer("❌ Будь ласка, введіть число (наприклад: 10 або 5.5)")

@dp.callback_query_handler(lambda c: c.data == "withdraw")
async def withdraw_info(callback: types.CallbackQuery):
    await bot.send_message(
        callback.from_user.id, 
        "📤 Для виведення коштів (від 200 💎) зверніться до техпідтримки: @ТвійЮзернейм"
    )
    await callback.answer()

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
