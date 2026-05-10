import os, uuid, logging, asyncio
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# --- НАЛАШТУВАННЯ ---
load_dotenv()
API_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")

logging.basicConfig(level=logging.INFO)
storage = MemoryStorage()
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=storage)

cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["game_bot_db"]
users_col = db["users"]
games_col = db["games"]

class GameState(StatesGroup):
    wait_bet = State()

def main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("👤 Профіль"), KeyboardButton("🎮 Боулінг"))
    markup.add(KeyboardButton("💎 Баланс"))
    return markup

async def get_u(user_id):
    u = await users_col.find_one({"_id": user_id})
    if not u:
        u = {"_id": user_id, "balance": 0.0}
        await users_col.insert_one(u)
    return u

# --- ЛОГІКА КИДКА ---
@dp.message_handler(content_types=types.ContentTypes.DICE, state="*")
async def handle_bowling_dice(m: types.Message):
    if m.dice.emoji != "🎳": return
    
    uid = m.from_user.id
    # Шукаємо АКТИВНУ гру для цього користувача
    g = await games_col.find_one({"status": "playing", "$or": [{"creator_id": uid}, {"opponent_id": uid}]})
    
    # 1. Якщо гри немає взагалі
    if not g:
        return await m.answer("Створіть гру щоб пограти з другом 🎳")

    # 2. Перевірка черги ходу
    if g.get('turn') != uid:
        return await m.answer("Зараз не ваш хід! Зачекайте суперника.")

    val = m.dice.value
    cid, oid = g['creator_id'], g['opponent_id']
    gid = g['game_id']
    
    # --- ХІД ГРАВЦЯ 1 (ТВОРЕЦЬ) ---
    if uid == cid:
        # Зберігаємо результат і передаємо хід супернику
        await games_col.update_one({"game_id": gid}, {"$set": {"c_score": val, "turn": oid}})
        
        await m.answer(f"У вас вибило {val}")
        await m.answer("Тепер хід суперника ⏳")
        
        await bot.send_message(oid, f"У суперника випало {val}")
        await bot.send_message(oid, "Тепер ваш хід 🎳 (натисніть, щоб копіювати): `🎳`", parse_mode="Markdown")
    
    # --- ХІД ГРАВЦЯ 2 (СУПЕРНИК) ---
    else:
        c_score = g.get('c_score', 0)
        o_score = val
        win_sum = round(g['bet'] * 2, 2)
        
        # ЗАВЕРШУЄМО ГРУ (статус finished блокує наступні кидки)
        await games_col.update_one({"game_id": gid}, {"$set": {"status": "finished", "o_score": val}})

        if c_score > o_score:
            await users_col.update_one({"_id": cid}, {"$inc": {"balance": win_sum}})
            res_c, res_o = "Вітаю 🎉 Ви перемогли!", "Нажаль ви програли 😢"
        elif o_score > c_score:
            await users_col.update_one({"_id": oid}, {"$inc": {"balance": win_sum}})
            res_o, res_c = "Вітаю 🎉 Ви перемогли!", "Нажаль ви програли 😢"
        else:
            await users_col.update_many({"_id": {"$in": [cid, oid]}}, {"$inc": {"balance": g['bet']}})
            res_c = res_o = "Нічия! Ставки повернуто 🤝"

        # Фінальні результати
        await bot.send_message(cid, f"Результат:\n\nВи: {c_score}\nСуперник: {o_score}\n\n{res_c}\n\nГра закінчена")
        await bot.send_message(oid, f"Результат:\n\nВи: {o_score}\nСуперник: {c_score}\n\n{res_o}\n\nГра закінчена")

# --- СТАРТ ТА ПРИЄДНАННЯ ---
@dp.message_handler(commands=['start'], state="*")
async def start_cmd(m: types.Message, state: FSMContext):
    args = m.get_args()
    uid = m.from_user.id
    if args and args.startswith("game_"):
        gid = args.replace("game_", "")
        g = await games_col.find_one({"game_id": gid, "status": "waiting"})
        u = await get_u(uid)
        
        if g and g['creator_id'] != uid:
            if u['balance'] < g['bet']: 
                return await m.answer("❌ Недостатньо коштів!")
            
            # Списуємо ставку і запускаємо гру
            await users_col.update_one({"_id": uid}, {"$inc": {"balance": -g['bet']}})
            await games_col.update_one({"game_id": gid}, {"$set": {
                "opponent_id": uid, 
                "status": "playing", 
                "turn": g['creator_id'] # ПЕРШИМ ЗАВЖДИ ХОДИТЬ ТВОРЕЦЬ
            }})
            
            info = f"Два гравці в зборі ✅\nСтавка: {g['bet']} 💎\nВиграш: {round(g['bet']*2, 2)} 💎"
            await bot.send_message(g['creator_id'], info)
            await bot.send_message(uid, info)
            await bot.send_message(g['creator_id'], "Ваш хід 🎳")
            await bot.send_message(uid, "Чекаємо на хід суперника...")
            return

    await m.answer("Вітаємо! Створіть гру в меню.", reply_markup=main_menu())

# --- СТВОРЕННЯ ГРИ ---
@dp.message_handler(lambda m: m.text == "🎮 Боулінг", state="*")
async def create_game(m: types.Message):
    await GameState.wait_bet.set()
    await m.answer("Введіть ставку 💎:", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add("❌ Скасувати"))

@dp.message_handler(state=GameState.wait_bet)
async def set_bet(m: types.Message, state: FSMContext):
    if m.text == "❌ Скасувати":
        await state.finish()
        return await m.answer("Скасовано", reply_markup=main_menu())
    
    try:
        bet = float(m.text)
        u = await get_u(m.from_user.id)
        if u['balance'] < bet: return await m.answer("❌ Мало коштів!")
        
        gid = str(uuid.uuid4())[:8]
        await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -bet}})
        await games_col.insert_one({
            "game_id": gid, "creator_id": m.from_user.id, "bet": bet, 
            "status": "waiting", "c_score": 0, "o_score": 0
        })
        
        me = await bot.get_me()
        await m.answer(f"🎳 Гра створена!\nПосилання для друга:\n`https://t.me/{me.username}?start=game_{gid}`", 
                       parse_mode="Markdown", reply_markup=main_menu())
        await state.finish()
    except:
        await m.answer("Введіть число!")

@dp.message_handler(lambda m: m.text == "💎 Баланс", state="*")
async def balance(m: types.Message):
    u = await get_u(m.from_user.id)
    await m.answer(f"💰 Баланс: {round(u['balance'], 2)} 💎")

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
