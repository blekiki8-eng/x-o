import os, uuid, logging
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()
API_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else 0
# Сюди вставте посилання на ваш сайт після завантаження на хостинг
WEB_APP_URL = "https://your-site-url.com" 

RATE = 44.50
logging.basicConfig(level=logging.INFO)
storage = MemoryStorage()
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=storage)

cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["game_bot_db"]
users_col = db["users"]
games_col = db["games"]

class DepositState(StatesGroup):
    wait_amount = State()
    wait_receipt = State()

class GameState(StatesGroup):
    wait_game_type = State()
    wait_size = State()
    wait_bet = State()

def main_menu(user_id):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("👤 Профіль"), KeyboardButton("🎮 Ігри"))
    markup.add(KeyboardButton("💎 Баланс"))
    return markup

async def get_u(m):
    uid = m.from_user.id
    u = await users_col.find_one({"_id": uid})
    if not u:
        u = {"_id": uid, "nickname": m.from_user.full_name, "balance": 0.0}
        await users_col.insert_one(u)
    return u

# --- ВХІД В ГРУ ---
@dp.message_handler(commands=['start'], state="*")
async def start(m: types.Message, state: FSMContext):
    await state.finish()
    u = await get_u(m)
    args = m.get_args()
    
    if args and args.startswith("game_"):
        gid = args.replace("game_", "")
        g = await games_col.find_one({"game_id": gid, "status": "waiting"})
        
        if g:
            # Створюємо кнопку для відкриття Web App для друга
            kb = InlineKeyboardMarkup().add(
                InlineKeyboardButton("Приєднатися до гри", web_app=WebAppInfo(url=f"{WEB_APP_URL}?gid={gid}"))
            )
            return await m.answer(f"🎮 Вас запросили у гру на {g['bet']} 💎", reply_markup=kb)
    
    await m.answer(f"Привіт, {u['nickname']}!", reply_markup=main_menu(m.from_user.id))

# --- СТВОРЕННЯ ГРИ ---
@dp.message_handler(lambda m: m.text == "🎮 Ігри", state="*")
async def games_menu(m: types.Message):
    await GameState.wait_game_type.set()
    kb = ReplyKeyboardMarkup(resize_keyboard=True).add("❌ Хрестики нолики", "❌ Скасувати")
    await m.answer("Оберіть гру:", reply_markup=kb)

@dp.message_handler(state=GameState.wait_game_type)
async def game_type(m: types.Message, state: FSMContext):
    if "Хрестики" in m.text:
        await GameState.wait_size.set()
        kb = ReplyKeyboardMarkup(resize_keyboard=True).add("3х3", "4х4", "❌ Скасувати")
        await m.answer("Розмір поля:", reply_markup=kb)

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
        if u['balance'] < bet: return await m.answer("❌ Недостатньо 💎")
        
        data = await state.get_data()
        gid = str(uuid.uuid4())[:8]
        
        await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -bet}})
        await games_col.insert_one({
            "game_id": gid, "creator_id": m.from_user.id, "bet": bet, 
            "size": data['sz'], "status": "waiting"
        })
        
        link = f"https://t.me/{(await bot.get_me()).username}?start=game_{gid}"
        # Кнопка для самого творця
        kb = InlineKeyboardMarkup().add(
            InlineKeyboardButton("Відкрити свою гру", web_app=WebAppInfo(url=f"{WEB_APP_URL}?gid={gid}"))
        )
        await m.answer(f"✅ Гра створена!\nВідправ посилання другу:\n`{link}`", parse_mode="Markdown", reply_markup=kb)
        await m.answer("Ви також можете зайти в гру через кнопку вище.", reply_markup=main_menu(m.from_user.id))
        await state.finish()
    except: await m.answer("Введіть число!")

# (Додайте сюди ваші обробники профілю та балансу з попередніх версій)

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
