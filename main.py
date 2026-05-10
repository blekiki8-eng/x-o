import os, uuid, logging
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()
API_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else 0

# Сюди ТРЕБА вставити посилання на твій РЕАЛЬНИЙ сайт з грою
WEB_APP_URL = "https://your-real-app.vercel.app" 

storage = MemoryStorage()
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=storage)

cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["game_bot_db"]
users_col = db["users"]
games_col = db["games"]

class GameState(StatesGroup):
    wait_game_type = State()
    wait_size = State()
    wait_bet = State()

def main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("👤 Профіль"), KeyboardButton("🎮 Ігри"))
    markup.add(KeyboardButton("💎 Баланс"))
    return markup

async def get_u(m):
    u = await users_col.find_one({"_id": m.from_user.id})
    if not u:
        u = {"_id": m.from_user.id, "nickname": m.from_user.full_name, "balance": 0.0}
        await users_col.insert_one(u)
    return u

# --- ЦІ ОБРОБНИКИ ТЕПЕР ПРАЦЮЮТЬ ЗАВЖДИ ---

@dp.message_handler(lambda m: m.text == "👤 Профіль", state="*")
async def profile(m: types.Message, state: FSMContext):
    await state.finish() # Скидає будь-яке меню "Ігор", якщо воно було відкрите
    u = await get_u(m)
    await m.answer(f"👤 **Профіль**\n\nНік: {u['nickname']}\nБаланс: {u['balance']} 💎", parse_mode="Markdown")

@dp.message_handler(lambda m: m.text == "💎 Баланс", state="*")
async def balance(m: types.Message, state: FSMContext):
    await state.finish() # Важливо!
    u = await get_u(m)
    await m.answer(f"💰 Ваш баланс: {u['balance']} 💎")

@dp.message_handler(lambda m: m.text == "❌ Скасувати", state="*")
async def cancel(m: types.Message, state: FSMContext):
    await state.finish()
    await m.answer("Дію скасовано", reply_markup=main_menu())

# --- МЕНЮ ІГОР ---

@dp.message_handler(lambda m: m.text == "🎮 Ігри", state="*")
async def games_menu(m: types.Message):
    await GameState.wait_game_type.set()
    kb = ReplyKeyboardMarkup(resize_keyboard=True).add("❌ Хрестики нолики", "❌ Скасувати")
    await m.answer("Оберіть гру:", reply_markup=kb)

@dp.message_handler(state=GameState.wait_game_type)
async def game_type(m: types.Message, state: FSMContext):
    if m.text == "❌ Хрестики нолики":
        await GameState.wait_size.set()
        kb = ReplyKeyboardMarkup(resize_keyboard=True).add("3х3", "4х4", "❌ Скасувати")
        await m.answer("Розмір поля:", reply_markup=kb)
    elif m.text == "❌ Скасувати":
        await state.finish()
        await m.answer("Меню", reply_markup=main_menu())

@dp.message_handler(state=GameState.wait_size)
async def game_size(m: types.Message, state: FSMContext):
    if m.text in ["3х3", "4х4"]:
        await state.update_data(sz=3 if "3" in m.text else 4)
        await GameState.wait_bet.set()
        await m.answer("Введіть ставку 💎:", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add("❌ Скасувати"))

@dp.message_handler(state=GameState.wait_bet)
async def game_bet(m: types.Message, state: FSMContext):
    try:
        bet = float(m.text)
        u = await get_u(m)
        if u['balance'] < bet: return await m.answer("❌ Мало 💎")
        
        data = await state.get_data()
        gid = str(uuid.uuid4())[:8]
        
        await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -bet}})
        await games_col.insert_one({"game_id": gid, "creator_id": m.from_user.id, "bet": bet, "status": "waiting"})
        
        # Створюємо кнопку Web App
        kb = InlineKeyboardMarkup().add(
            InlineKeyboardButton("Запустити гру", web_app=WebAppInfo(url=f"{WEB_APP_URL}?gid={gid}"))
        )
        
        link = f"https://t.me/{(await bot.get_me()).username}?start=game_{gid}"
        await m.answer(f"✅ Гра створена!\nПосилання: `{link}`", parse_mode="Markdown", reply_markup=main_menu())
        await m.answer("Тисни кнопку для входу:", reply_markup=kb)
        await state.finish()
    except:
        await m.answer("Введіть число!")

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
