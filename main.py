import os
import uuid
import logging
import asyncio
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

# --- Конфігурація ---
API_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

EXCHANGE_RATE = 44.5  # 1💎 = 44.5₴
BONUS_PERCENT = 0.05  # +5% до суми в гривнях

logging.basicConfig(level=logging.INFO)
storage = MemoryStorage()
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=storage)

# Підключення до БД
cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["fishcash_game"]
users_col = db["users"]
games_col = db["games"]

# --- Стани (FSM) ---
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

def get_board_markup(game_id, board):
    markup = InlineKeyboardMarkup(row_width=3)
    buttons = []
    for i in range(9):
        char = board[i] if board[i] != " " else "⬜️"
        buttons.append(InlineKeyboardButton(char, callback_data=f"tic_{game_id}_{i}"))
    markup.add(*buttons)
    return markup

def admin_confirm_kb(user_id, amount):
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("✅ Прийняти", callback_data=f"adm_confirm_{user_id}_{amount}"),
        InlineKeyboardButton("❌ Відхилити", callback_data=f"adm_reject_{user_id}")
    )
    return markup

# --- Допоміжні функції ---
async def get_user(user_id, username="Гравець"):
    user = await users_col.find_one({"_id": user_id})
    if not user:
        user = {"_id": user_id, "username": username, "balance": 0}
        await users_col.insert_one(user)
    return user

def check_winner(b):
    win_coords = [(0,1,2), (3,4,5), (6,7,8), (0,3,6), (1,4,7), (2,5,8), (0,4,8), (2,4,6)]
    for r in win_coords:
        if b[r[0]] == b[r[1]] == b[r[2]] != " ": return b[r[0]]
    if " " not in b: return "draw"
    return None

# --- Обробники Команд ---
@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    await get_user(message.from_user.id, message.from_user.username)
    args = message.get_args()
    
    # Якщо гравець зайшов по лінку на гру
    if args.startswith("game_"):
        game_id = args.split("_")[1]
        game = await games_col.find_one({"game_id": game_id, "status": "waiting"})
        
        if not game: return await message.answer("❌ Гра не знайдена.")
        if game['creator_id'] == message.from_user.id: return await message.answer("❌ Не можна грати з собою.")
        
        user = await get_user(message.from_user.id)
        if user['balance'] < game['bet']: return await message.answer("❌ Недостатньо 💎 для ставки.")

        await users_col.update_one({"_id": message.from_user.id}, {"$inc": {"balance": -game['bet']}})
        await games_col.update_one({"game_id": game_id}, {"$set": {"opponent_id": message.from_user.id, "status": "playing", "turn": game['creator_id']}})
        
        await message.answer(f"🎮 Гра почалася! Ставка: {game['bet']} 💎", reply_markup=main_menu())
        await bot.send_message(game['creator_id'], "✅ Суперник приєднався! Твій хід (X):", reply_markup=get_board_markup(game_id, game['board']))
        return

    await message.answer("Вітаємо у FishCash! 💎 Використовуйте кнопки:", reply_markup=main_menu())

# --- Обробники Меню ---
@dp.message_handler(lambda m: m.text == "👤 Профіль")
async def profile(message: types.Message):
    user = await get_user(message.from_user.id)
    await message.answer(f"👤 **Профіль**\n\n🆔 ID: `{user['_id']}`\n💰 Баланс: {user['balance']} 💎", parse_mode="Markdown")

@dp.message_handler(lambda m: m.text == "💎 Баланс")
async def balance(message: types.Message):
    user = await get_user(message.from_user.id)
    await message.answer(f"💎 **Ваш баланс: {user['balance']} 💎**", reply_markup=balance_keyboard(), parse_mode="Markdown")

@dp.message_handler(lambda m: m.text == "🎮 Ігри")
async def games_list(message: types.Message):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("❌ Хрестики-Нолики ⭕️", callback_data="create_tic"))
    await message.answer("Оберіть гру:", reply_markup=markup)

# --- Логіка Поповнення ---
@dp.callback_query_handler(lambda c: c.data == "deposit")
async def dep_1(callback: types.CallbackQuery):
    await bot.send_message(callback.from_user.id, "💰 Введіть суму в 💎 (наприклад: 10):")
    await DepositState.wait_amount.set()
    await callback.answer()

@dp.message_handler(state=DepositState.wait_amount)
async def dep_2(message: types.Message, state: FSMContext):
    try:
        amt = float(message.text)
        if amt <= 0: return await message.answer("❌ Сума має бути більше 0")
        
        total_uah = (amt * EXCHANGE_RATE) * (1 + BONUS_PERCENT)
        await state.update_data(amount=amt)
        
        msg = (f"📑 **Заявка: {amt} 💎**\n📈 Курс: 1 💎 = {EXCHANGE_RATE}₴\n🎁 Бонус: +5%\n"
               f"💵 **До оплати: {total_uah:.2f}₴**\n\n💳 Картка: `5355 2800 2890 2177`\n\n📸 **Надішліть фото чека:**")
        await message.answer(msg, parse_mode="Markdown")
        await DepositState.wait_receipt.set()
    except: await message.answer("❌ Введіть число.")

@dp.message_handler(state=DepositState.wait_receipt, content_types=['photo'])
async def dep_3(message: types.Message, state: FSMContext):
    data = await state.get_data()
    await bot.send_photo(ADMIN_ID, message.photo[-1].file_id, 
                         caption=f"🔔 Новий чек!\nЮзер: @{message.from_user.username}\nID: {message.from_user.id}\n💎 Сума: {data['amount']}",
                         reply_markup=admin_confirm_kb(message.from_user.id, data['amount']))
    await message.answer("✅ Чек надіслано! Очікуйте перевірки.")
    await state.finish()

# --- Логіка Адмінки ---
@dp.callback_query_handler(lambda c: c.data.startswith('adm_'))
# --- Логіка Адмінки ---
@dp.callback_query_handler(lambda c: c.data.startswith('adm_'))
async def admin_decision(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID: 
        return await callback.answer("Ви не адмін!")
        
    p = callback.data.split("_")
    action = p[1]
    uid = int(p[2])
    amt = float(p[3]) if action == "confirm" else 0
    
    if action == "confirm":
        await users_col.update_one({"_id": uid}, {"$inc": {"balance": amt}})
        try:
            await bot.send_message(uid, f"✅ Ваш баланс поповнено на {amt} 💎! Приємної гри.")
        except:
            pass
        await callback.message.edit_caption(callback.message.caption + "\n\n✅ **ПРИЙНЯТО**", parse_mode="Markdown")
    else:
        try:
            await bot.send_message(uid, "❌ Вашу заявку на поповнення відхилено.")
        except:
            pass
        await callback.message.edit_caption(callback.message.caption + "\n\n❌ **ВІДХИЛЕНО**", parse_mode="Markdown")
    
    await callback.answer()
