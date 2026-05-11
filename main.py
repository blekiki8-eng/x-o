import os, uuid, logging, asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, 
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# --- КОНФІГУРАЦІЯ ---
load_dotenv()
API_TOKEN = os.getenv("BOT_TOKEN") 
MONGO_URL = os.getenv("MONGO_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CURRATE = 44.50 

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["game_bot_db"] 
users_col = db["users"]
games_col = db["games"]

# --- СТАНИ (FSM) ---
class GameStates(StatesGroup):
    wait_rounds = State() # Очікуємо вибір раундів
    wait_bet = State()    # Очікуємо введення ставки

class DepositStates(StatesGroup):
    wait_amount = State()
    wait_receipt = State()

# --- ДОПОМІЖНІ ФУНКЦІЇ ---
async def get_u(uid):
    u = await users_col.find_one({"_id": uid})
    if not u:
        u = {"_id": uid, "balance": 0.0, "referals_count": 0, "turnover": 0.0, "games_played": 0}
        await users_col.insert_one(u)
    return u

def main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="👤 Профіль"), KeyboardButton(text="🎮 Games")],
        [KeyboardButton(text="💎 Баланс"), KeyboardButton(text="🤝 Рефералка")],
        [KeyboardButton(text="⚙️ Налаштування")]
    ], resize_keyboard=True)

def rounds_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="1 кидок"), KeyboardButton(text="5 кидків")],
        [KeyboardButton(text="⬅️ Назад")]
    ], resize_keyboard=True)

# --- КОМАНДИ ---

@dp.message(Command("start"))
async def cmd_start(m: types.Message, state: FSMContext):
    await state.clear()
    await get_u(m.from_user.id)
    if len(m.text.split()) > 1:
        param = m.text.split()[1]
        if param.startswith("game_"):
            return await join_game_logic(m, param.replace("game_", ""))
    await m.answer("💎 Вітаємо!", reply_markup=main_kb())

# --- ГОЛОВНА ЛОГІКА СТВОРЕННЯ ГРИ (ЯК ТИ ПРОСИВ) ---

@dp.message(F.text == "🎮 Games")
async def games_menu(m: types.Message):
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🎳"), KeyboardButton(text="🎲")], [KeyboardButton(text="⬅️ Назад")]], resize_keyboard=True)
    await m.answer("Оберіть гру:", reply_markup=kb)

@dp.message(F.text.in_(["🎳", "🎲"]))
async def choose_game_step(m: types.Message, state: FSMContext):
    # КРОК 1: Гравець обрав гру, тепер питаємо раунди
    await state.update_data(g_type=m.text)
    await m.answer("Оберіть кількість раундів:", reply_markup=rounds_kb())
    await state.set_state(GameStates.wait_rounds)

@dp.message(GameStates.wait_rounds, F.text.in_(["1 кидок", "5 кидків"]))
async def choose_rounds_step(m: types.Message, state: FSMContext):
    # КРОК 2: Гравець обрав раунди, тепер питаємо ставку
    r = 1 if "1" in m.text else 5
    await state.update_data(rounds=r)
    await m.answer(f"Ви обрали {r} раундів. Тепер введіть суму ставки (мінімум 0.05 💎):", reply_markup=ReplyKeyboardRemove())
    await state.set_state(GameStates.wait_bet)

@dp.message(GameStates.wait_bet)
async def enter_bet_step(m: types.Message, state: FSMContext):
    # КРОК 3: Гравець ввів ставку, створюємо посилання
    try:
        bet = float(m.text.replace(",", "."))
        if bet < 0.05: return await m.answer("❌ Мінімум 0.05")
        u = await get_u(m.from_user.id)
        if u['balance'] < bet: return await m.answer("❌ Недостатньо балансу!")
        
        data = await state.get_data()
        gid = str(uuid.uuid4())[:8]
        
        await games_col.insert_one({
            "game_id": gid, "creator_id": m.from_user.id, "opponent_id": None,
            "type": data['g_type'], "bet": bet, "rounds_total": data['rounds'],
            "status": "waiting", "c_score": [], "o_score": [], "turn": m.from_user.id
        })
        
        me = await bot.get_me()
        link = f"https://t.me/{me.username}?start=game_{gid}"
        
        await m.answer(f"✅ **Посилання на гру:**\n\n`{link}`\n\nПерешліть це посилання другу щоб разом зіграти", 
                       reply_markup=main_kb(), parse_mode="Markdown")
        await state.clear()
    except:
        await m.answer("❌ Введіть число (наприклад 0.1)")

# --- ПРИЄДНАННЯ ТА ІГРОВИЙ ПРОЦЕС ---

async def join_game_logic(m, gid):
    g = await games_col.find_one({"game_id": gid, "status": "waiting"})
    if not g: return await m.answer("❌ Гра вже зайнята або не існує")
    if g['creator_id'] == m.from_user.id: return await m.answer("❌ Не можна грати з собою")
    
    u = await get_u(m.from_user.id)
    if u['balance'] < g['bet']: return await m.answer("❌ Мало 💎")
    
    await users_col.update_many({"_id": {"$in": [g['creator_id'], m.from_user.id]}}, {"$inc": {"balance": -g['bet']}})
    await games_col.update_one({"game_id": gid}, {"$set": {"opponent_id": m.from_user.id, "status": "playing"}})
    
    msg = f"Гра почалася! 🎲\nСтавка: {g['bet']} 💎\n\nПершим ходить творець матчу!"
    await bot.send_message(g['creator_id'], msg + "\n\n**Ваш хід! Кидайте емодзі!**")
    await m.answer(msg + "\n\nОчікуйте ходу суперника...")

@dp.message(F.dice)
async def handle_dice(m: types.Message):
    # Шукаємо ТІЛЬКИ активні ігри
    g = await games_col.find_one({"status": "playing", "$or": [{"creator_id": m.from_user.id}, {"opponent_id": m.from_user.id}]})
    if not g: return # Ігноруємо кидки, якщо гра не почалася
    
    if g['turn'] != m.from_user.id: return
    
    emoji = "🎳" if g['type'] == "🎳" else "🎲"
    if m.dice.emoji != emoji: return
    
    val = m.dice.value
    is_c = (m.from_user.id == g['creator_id'])
    field = "c_score" if is_c else "o_score"
    next_p = g['opponent_id'] if is_c else g['creator_id']
    
    await games_col.update_one({"game_id": g['game_id']}, {"$push": {field: val}, "$set": {"turn": next_p}})
    await asyncio.sleep(3)
    
    upd = await games_col.find_one({"game_id": g['game_id']})
    if len(upd['c_score']) == upd['rounds_total'] and len(upd['o_score']) == upd['rounds_total']:
        # Розрахунок результатів
        sc, so = sum(upd['c_score']), sum(upd['o_score'])
        res = f"Фінал! 🏁\nВи: {sc if is_c else so}\nСуперник: {so if is_c else sc}\n\n"
        if sc == so:
            await users_col.update_many({"_id": {"$in": [g['creator_id'], g['opponent_id']]}}, {"$inc": {"balance": g['bet']}})
            txt = res + "Нічия! Баланс повернуто."
            await bot.send_message(g['creator_id'], txt); await bot.send_message(g['opponent_id'], txt)
        else:
            win_id = g['creator_id'] if sc > so else g['opponent_id']
            lose_id = g['opponent_id'] if sc > so else g['creator_id']
            await users_col.update_one({"_id": win_id}, {"$inc": {"balance": g['bet']*1.98}})
            await bot.send_message(win_id, res + f"Виграш: {g['bet']*1.98:.2f} 💎")
            await bot.send_message(lose_id, res + "Ви програли ❌")
        await games_col.update_one({"game_id": g['game_id']}, {"$set": {"status": "finished"}})
    else:
        await bot.send_message(next_p, f"Ваш хід! Кидайте {emoji}")

# --- БАЛАНС / АДМІН ---

@dp.message(F.text == "💎 Баланс")
async def balance_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Поповнити", callback_data="dep")]])
    await m.answer(f"💰 Ваш баланс: `{u['balance']:.2f}` 💎", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "dep")
async def dep_start(cb: types.CallbackQuery, state: FSMContext):
    await cb.message.answer("Сума поповнення:")
    await state.set_state(DepositStates.wait_amount)

@dp.message(DepositStates.wait_amount)
async def dep_amt(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(",", "."))
        await state.update_data(amt=amt)
        await m.answer(f"Надішліть квитанцію (фото/файл). До оплати: {amt*CURRATE:.2f} ГРН")
        await state.set_state(DepositStates.wait_receipt)
    except: await m.answer("Введіть число")

@dp.message(DepositStates.wait_receipt, F.photo | F.document)
async def dep_rec(m: types.Message, state: FSMContext):
    data = await state.get_data(); uid = m.from_user.id; amt = data.get('amt')
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅", callback_data=f"ap_ok_{uid}_{amt}"),
        InlineKeyboardButton(text="❌", callback_data=f"ap_no_{uid}")
    ]])
    await bot.send_message(ADMIN_ID, f"Нова заявка від {uid} на {amt} 💎", reply_markup=kb)
    await m.answer("Чекайте підтвердження!"); await state.clear()

@dp.callback_query(F.data.startswith("ap_"))
async def admin_done(cb: types.CallbackQuery):
    d = cb.data.split("_")
    action, target_id = d[1], int(d[2])
    if action == "ok":
        amount = float(d[3])
        await users_col.update_one({"_id": target_id}, {"$inc": {"balance": amount}})
        await bot.send_message(target_id, f"✅ Баланс поповнено на {amount} 💎")
        await cb.message.edit_text("Схвалено ✅")
    else:
        await bot.send_message(target_id, "❌ Заявку відхилено.")
        await cb.message.edit_text("Відхилено ❌")
    await cb.answer()

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
