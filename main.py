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

API_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID")) # Твій ID

EXCHANGE_RATE = 44.5
BONUS_PERCENT = 0.05

logging.basicConfig(level=logging.INFO)
storage = MemoryStorage()
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=storage)

cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["fishcash_game"]
users_col = db["users"]

class DepositState(StatesGroup):
    wait_amount = State()
    wait_receipt = State()

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

def admin_confirm_kb(user_id, amount):
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("✅ Прийняти", callback_data=f"adm_confirm_{user_id}_{amount}"),
        InlineKeyboardButton("❌ Відхилити", callback_data=f"adm_reject_{user_id}")
    )
    return markup

# --- Функції БД ---

async def get_user(user_id, username="Гравець"):
    user = await users_col.find_one({"_id": user_id})
    if not user:
        user = {"_id": user_id, "username": username, "balance": 0}
        await users_col.insert_one(user)
    return user

# --- Обробники ---

@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    await get_user(message.from_user.id, message.from_user.username)
    await message.answer("Вітаємо у FishCash Game! 💎", reply_markup=main_menu())

@dp.message_handler(lambda m: m.text == "💎 Баланс")
async def show_balance(message: types.Message):
    user = await get_user(message.from_user.id)
    await message.answer(
        f"💎 **Ваш баланс: {user['balance']} 💎**",
        reply_markup=balance_keyboard(),
        parse_mode="Markdown"
    )

@dp.callback_query_handler(lambda c: c.data == "deposit")
async def deposit_start(callback: types.CallbackQuery):
    await bot.send_message(callback.from_user.id, "💰 Введіть суму в 💎 (доларів), яку хочете поповнити:")
    await DepositState.wait_amount.set()
    await callback.answer()

@dp.message_handler(state=DepositState.wait_amount)
async def process_amount(message: types.Message, state: FSMContext):
    try:
        amount_diamonds = float(message.text)
        if amount_diamonds <= 0:
            return await message.answer("❌ Сума має бути більшою за 0.")
        
        total_uah = (amount_diamonds * EXCHANGE_RATE) * (1 + BONUS_PERCENT)
        
        await state.update_data(amount=amount_diamonds)
        
        response = (
            f"📑 **Заявка: {amount_diamonds} 💎**\n"
            f"📈 Курс: 1 💎 = {EXCHANGE_RATE}₴\n"
            f"🎁 Бонус за поповнення: +5%\n"
            f"💵 **До оплати: {total_uah:.2f}₴**\n\n"
            f"💳 Реквізити:\n`5355 2800 2890 2177`\n\n"
            f"📸 **Надішліть фото квитанції одним повідомленням:**"
        )
        await message.answer(response, parse_mode="Markdown")
        await DepositState.wait_receipt.set()
        
    except ValueError:
        await message.answer("❌ Введіть число.")

@dp.message_handler(state=DepositState.wait_receipt, content_types=['photo', 'document'])
async def process_receipt(message: types.Message, state: FSMContext):
    data = await state.get_data()
    amount = data.get("amount")
    user_id = message.from_user.id
    username = message.from_user.username or "NoName"

    # Повідомлення адміну
    caption = f"🔔 **Нова заявка на поповнення!**\n\n👤 Гравць: @{username} (ID: `{user_id}`)\n💎 Сума: {amount} 💎"
    
    if message.photo:
        await bot.send_photo(ADMIN_ID, message.photo[-1].file_id, caption=caption, 
                             reply_markup=admin_confirm_kb(user_id, amount), parse_mode="Markdown")
    else:
        await bot.send_document(ADMIN_ID, message.document.file_id, caption=caption, 
                                reply_markup=admin_confirm_kb(user_id, amount), parse_mode="Markdown")

    await message.answer("✅ Квитанцію надіслано адміну! Очікуйте нарахування 💎.")
    await state.finish()

# --- Адмінські дії ---

@dp.callback_query_handler(lambda c: c.data.startswith('adm_'))
async def admin_action(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return await callback.answer("Ви не адмін!")

    parts = callback.data.split("_")
    action = parts[1]
    target_id = int(parts[2])
    
    if action == "confirm":
        amount = float(parts[3])
        await users_col.update_one({"_id": target_id}, {"$inc": {"balance": amount}})
        
        await bot.send_message(target_id, f"✅ Ваш баланс поповнено на {amount} 💎! Приємної гри.")
        await callback.message.edit_caption(callback.message.caption + "\n\n✅ **ПРИЙНЯТО**", parse_mode="Markdown")
    
    elif action == "reject":
        await bot.send_message(target_id, "❌ Вашу заявку на поповнення відхилено. Перевірте дані або зверніться до адміна.")
        await callback.message.edit_caption(callback.message.caption + "\n\n❌ **ВІДХИЛЕНО**", parse_mode="Markdown")

    await callback.answer()

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
