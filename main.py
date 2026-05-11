import os, uuid, logging, asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# --- НАЛАШТУВАННЯ ---
load_dotenv()
API_TOKEN = os.getenv("BOT_TOKEN") 
MONGO_URL = os.getenv("MONGO_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["game_bot_db"] 
users_col = db["users"]
games_col = db["games"]

class GameStates(StatesGroup):
    wait_bet = State()

# --- КЛАВІАТУРИ ---
def main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="👤 Профіль"), KeyboardButton(text="🎮 Games")],
        [KeyboardButton(text="💎 Баланс")]
    ], resize_keyboard=True)

def games_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🎳"), KeyboardButton(text="🎲")],
        [KeyboardButton(text="⬅️ Назад")]
    ], resize_keyboard=True)

# --- ДОПОМІЖНІ ФУНКЦІЇ ---
async def get_u(uid):
    u = await users_col.find_one({"_id": uid})
    if not u:
        u = {"_id": uid, "balance": 10.0} # Даємо 10 для тесту
        await users_col.insert_one(u)
    return u

# --- ЛОГІКА СТВОРЕННЯ МАТЧУ ---

@dp.message(Command("start"))
async def cmd_start(m: types.Message, state: FSMContext):
    await state.clear()
    await get_u(m.from_user.id)
    # Перевірка, чи це вхід за посиланням на гру
    if len(m.text.split()) > 1:
        param = m.text.split()[1]
        if param.startswith("game_"):
            return await join_game_logic(m, param.replace("game_", ""))
    await m.answer("Вітаємо!", reply_markup=main_kb())

@dp.message(F.text == "🎮 Games")
async def games_menu(m: types.Message):
    await m.answer("Оберіть гру:", reply_markup=games_kb())

@dp.message(F.text.in_(["🎳", "🎲"]))
async def choose_game(m: types.Message, state: FSMContext):
    # 1. Після натискання на гру — просимо ставку
    await state.update_data(g_type=m.text)
    await m.answer("Введіть ставку (мінімум 0.05):", reply_markup=ReplyKeyboardRemove())
    await state.set_state(GameStates.wait_bet)

@dp.message(GameStates.wait_bet)
async def enter_bet(m: types.Message, state: FSMContext):
    # 2. Після введення ставки — даємо посилання
    try:
        bet = float(m.text.replace(",", "."))
        u = await get_u(m.from_user.id)
        if u['balance'] < bet: return await m.answer("❌ Недостатньо коштів")
        
        data = await state.get_data()
        gid = str(uuid.uuid4())[:8]
        
        await games_col.insert_one({
            "game_id": gid, "creator_id": m.from_user.id, "opponent_id": None,
            "type": data['g_type'], "bet": bet, "status": "waiting"
        })
        
        me = await bot.get_me()
        link = f"https://t.me/{me.username}?start=game_{gid}"
        await m.answer(f"Посилання на гру: {link}\n\nПерешліть це посилання другу щоб разом зіграти", reply_markup=main_kb())
        await state.clear()
    except:
        await m.answer("Введіть число")

# --- ЛОГІКА КОЛИ ЗАЙШОВ ГРАВЕЦЬ 2 ---

async def join_game_logic(m, gid):
    g = await games_col.find_one({"game_id": gid, "status": "waiting"})
    if not g or g['creator_id'] == m.from_user.id: 
        return await m.answer("❌ Гра недоступна")
    
    u2 = await get_u(m.from_user.id)
    if u2['balance'] < g['bet']: return await m.answer("❌ Мало коштів")
    
    # Списуємо ставку в обох
    await users_col.update_one({"_id": g['creator_id']}, {"$inc": {"balance": -g['bet']}})
    await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -g['bet']}})
    
    # Оновлюємо статус гри
    await games_col.update_one({"game_id": gid}, {
        "$set": {
            "opponent_id": m.from_user.id, 
            "status": "playing",
            "turn": g['creator_id'], # Першим ходить Гравець 1
            "c_score": None, "o_score": None
        }
    })
    
    msg = "Гра створена ✅"
    # Повідомляємо обох
    await bot.send_message(g['creator_id'], f"{msg}\n\nВаш хід! Кидайте стікер {g['type']}")
    await m.answer(f"{msg}\n\nОчікуйте ходу Гравця 1")

# --- ОБРОБКА КИДКІВ (DICE) ---

@dp.message(F.dice)
async def handle_dice(m: types.Message):
    # Шукаємо активну гру, де бере участь цей юзер
    g = await games_col.find_one({
        "status": "playing", 
        "$or": [{"creator_id": m.from_user.id}, {"opponent_id": m.from_user.id}]
    })
    if not g or g['turn'] != m.from_user.id: return
    
    # Перевірка, чи правильний стікер (кубик/боулінг)
    emoji = "🎳" if g['type'] == "🎳" else "🎲"
    if m.dice.emoji != emoji: return
    
    val = m.dice.value
    is_creator = (m.from_user.id == g['creator_id'])
    
    if is_creator:
        await games_col.update_one({"game_id": g['game_id']}, {"$set": {"c_score": val, "turn": g['opponent_id']}})
        await asyncio.sleep(3)
        await bot.send_message(g['opponent_id'], f"Гравець 1 кинув на {val} очок.\nТепер ваш хід! Кидайте {emoji}")
    else:
        # Гравець 2 кинув — фінал
        await games_col.update_one({"game_id": g['game_id']}, {"$set": {"o_score": val, "status": "finished"}})
        await asyncio.sleep(3)
        
        c_score = g['c_score']
        o_score = val
        
        res = f"Результати:\nГравець 1: {c_score}\nГравець 2: {o_score}\n\n"
        
        if c_score > o_score:
            win_id = g['creator_id']
            await bot.send_message(g['creator_id'], res + "Ви перемогли! 🏆"); await bot.send_message(g['opponent_id'], res + "Ви програли ❌")
        elif o_score > c_score:
            win_id = g['opponent_id']
            await bot.send_message(g['opponent_id'], res + "Ви перемогли! 🏆"); await bot.send_message(g['creator_id'], res + "Ви програли ❌")
        else:
            win_id = None
            await bot.send_message(g['creator_id'], res + "Нічия!"); await bot.send_message(g['opponent_id'], res + "Нічия!")
        
        if win_id:
            await users_col.update_one({"_id": win_id}, {"$inc": {"balance": g['bet'] * 1.95}})
        else:
            # Повертаємо ставки при нічиї
            await users_col.update_one({"_id": g['creator_id']}, {"$inc": {"balance": g['bet']}})
            await users_col.update_one({"_id": g['opponent_id']}, {"$inc": {"balance": g['bet']}})

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
