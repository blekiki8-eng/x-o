import os, uuid, logging, asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, 
    InlineKeyboardMarkup, InlineKeyboardButton
)
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# --- КОНФІГУРАЦІЯ ---
load_dotenv()
API_TOKEN = os.getenv("BOT_TOKEN") 
MONGO_URL = os.getenv("MONGO_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CURRATE = 44.50  # Курс 1 💎 до ГРН

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["game_bot_db"] 
users_col = db["users"]
games_col = db["games"]
finance_col = db["finance"]
stats_col = db["stats"]

# --- СТАНИ FSM ---
class GameStates(StatesGroup):
    wait_rounds = State()
    wait_bet = State()

class FinanceStates(StatesGroup):
    wait_dep_amount = State()
    wait_receipt = State()
    wait_draw_sum = State()
    wait_draw_details = State()

# --- КЛАВІАТУРИ ---
def main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="👤 Профіль"), KeyboardButton(text="🎮 Games")],
        [KeyboardButton(text="💎 Баланс"), KeyboardButton(text="🤝 Рефералка")],
        [KeyboardButton(text="⚙️ Налаштування")]
    ], resize_keyboard=True)

def cancel_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Скасувати")]], resize_keyboard=True)

async def get_u(uid, ref_id=None):
    u = await users_col.find_one({"_id": uid})
    if not u:
        u = {"_id": uid, "balance": 0.0, "referals_count": 0, "turnover": 0.0, "games_played": 0, "referrer": ref_id}
        await users_col.insert_one(u)
        if ref_id and ref_id != uid:
            await users_col.update_one({"_id": ref_id}, {"$inc": {"referals_count": 1}})
    return u

# --- АДМІН ПАНЕЛЬ ---
@dp.message(Command("admin"))
async def admin_panel(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    
    # Отримуємо загальну статистику (касу)
    stats = await stats_col.find_one({"_id": "global"})
    admin_bal = stats.get("admin_balance", 0.0) if stats else 0.0
    
    # ТОП-10 за оборотом
    text = f"👑 **Адмін-панель**\n\n💰 Каса адміна (комісії): `{admin_bal:.2f} 💎`\n"
    text += "────────────────\n🏆 **ТОП-10 гравців за оборотом:**\n"
    
    top_users = users_col.find().sort("turnover", -1).limit(10)
    i = 1
    async for user in top_users:
        text += f"{i}. ID: `{user['_id']}` | Оборот: `{user.get('turnover', 0):.2f}` | 👥 Реф: {user.get('referals_count', 0)}\n"
        i += 1
    
    await m.answer(text, parse_mode="Markdown")

# --- СИСТЕМА ГРИ ---
@dp.message(Command("start"))
async def cmd_start(m: types.Message, state: FSMContext):
    await state.clear()
    args = m.text.split()
    user_id = m.from_user.id
    ref_id = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
    u = await get_u(user_id, ref_id)
    
    if len(args) > 1 and args[1].startswith("game_"):
        gid = args[1].replace("game_", "").strip()
        g = await games_col.find_one({"game_id": gid, "status": "waiting"})
        if not g: return await m.answer("❌ Гра не знайдена.", reply_markup=main_kb())
        if g['creator_id'] == user_id: return await m.answer("❌ Ви не можете грати самі з собою.")
        if u['balance'] < g['bet']: return await m.answer(f"❌ Потрібно {g['bet']} 💎")
        
        await users_col.update_many({"_id": {"$in": [g['creator_id'], user_id]}}, {"$inc": {"balance": -g['bet']}})
        await games_col.update_one({"game_id": gid}, {"$set": {"opponent_id": user_id, "status": "playing", "turn": g['creator_id']}})
        await bot.send_message(g['creator_id'], f"🎮 Гравець приєднався! Твій хід: `{g['type']}`", parse_mode="Markdown")
        return await m.answer(f"🎮 Ви приєдналися! Очікуйте хід суперника: `{g['type']}`", reply_markup=main_kb(), parse_mode="Markdown")
    
    await m.answer("💎 Ласкаво просимо до Different Games!", reply_markup=main_kb())

@dp.message(F.text == "🎮 Games")
async def games_menu(m: types.Message):
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🎳 Боулінг"), KeyboardButton(text="🎲 Кубик")], [KeyboardButton(text="❌ Скасувати")]], resize_keyboard=True)
    await m.answer("Оберіть режим гри:", reply_markup=kb)

@dp.message(F.text.in_(["🎳 Боулінг", "🎲 Кубик"]))
async def g_mode(m: types.Message, state: FSMContext):
    await state.update_data(type="🎳" if "🎳" in m.text else "🎲")
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="1"), KeyboardButton(text="5")], [KeyboardButton(text="❌ Скасувати")]], resize_keyboard=True)
    await m.answer("Оберіть кількість раундів:", reply_markup=kb)
    await state.set_state(GameStates.wait_rounds)

@dp.message(GameStates.wait_rounds, F.text.in_(["1", "5"]))
async def g_rounds(m: types.Message, state: FSMContext):
    await state.update_data(r=int(m.text))
    await m.answer(f"Введіть суму ставки (мін. 0.07 💎):", reply_markup=cancel_kb())
    await state.set_state(GameStates.wait_bet)

@dp.message(GameStates.wait_bet)
async def g_create(m: types.Message, state: FSMContext):
    if m.text == "❌ Скасувати":
        await state.clear()
        return await m.answer("Скасовано.", reply_markup=main_kb())
    try:
        bet = float(m.text.replace(",", "."))
        u = await get_u(m.from_user.id); d = await state.get_data()
        if u['balance'] < bet: return await m.answer("❌ Недостатньо коштів.")
        
        gid = str(uuid.uuid4())[:8]
        await games_col.insert_one({"game_id": gid, "creator_id": m.from_user.id, "status": "waiting", "type": d['type'], "bet": bet, "rounds_total": d['r'], "c_score": [], "o_score": [], "opponent_id": None})
        me = await bot.get_me()
        await m.answer(f"✅ Гра створена!\n🔗 Посилання: `https://t.me/{me.username}?start=game_{gid}`", reply_markup=main_kb(), parse_mode="Markdown")
        await state.clear()
    except: await m.answer("Введіть число!")

@dp.message(F.dice)
async def dice_handler(m: types.Message):
    g = await games_col.find_one({"status": "playing", "turn": m.from_user.id})
    if not g or m.dice.emoji != g['type']: return
    
    opp_id = g['opponent_id'] if m.from_user.id == g['creator_id'] else g['creator_id']
    f = "c_score" if m.from_user.id == g['creator_id'] else "o_score"
    
    await bot.send_message(opp_id, f"Суперник кинув `{g['type']}`:")
    await m.forward(opp_id)
    await asyncio.sleep(4)
    await games_col.update_one({"game_id": g['game_id']}, {"$push": {f: m.dice.value}})
    
    upd = await games_col.find_one({"game_id": g['game_id']})
    if len(upd['c_score']) == upd['rounds_total'] and len(upd['o_score']) == upd['rounds_total']:
        await finish_game(upd)
    else:
        await games_col.update_one({"game_id": g['game_id']}, {"$set": {"turn": opp_id}})
        await bot.send_message(opp_id, f"Твій хід! Натисни: `{g['type']}`", parse_mode="Markdown")

async def finish_game(g):
    s1, s2 = sum(g['c_score']), sum(g['o_score'])
    for uid in [g['creator_id'], g['opponent_id']]:
        my_s, op_s = (s1, s2) if uid == g['creator_id'] else (s2, s1)
        u = await users_col.find_one({"_id": uid})
        if s1 == s2:
            await users_col.update_one({"_id": uid}, {"$inc": {"balance": g['bet']}})
            await bot.send_message(uid, f"🏁 Результат: {my_s}:{op_s}\n🤝 Нічия! Повернення.")
        elif my_s > op_s:
            raw_win = g['bet'] * 2
            # Розрахунок комісії
            ref_part = 0.0
            if u.get("referrer"):
                ref_part = raw_win * 0.01
                await users_col.update_one({"_id": u['referrer']}, {"$inc": {"balance": ref_part}})
                admin_part = raw_win * 0.01
            else:
                admin_part = raw_win * 0.02
            
            win_net = raw_win - admin_part - ref_part
            await users_col.update_one({"_id": uid}, {"$inc": {"balance": win_net, "turnover": g['bet'], "games_played": 1}})
            await stats_col.update_one({"_id": "global"}, {"$inc": {"admin_balance": admin_part}}, upsert=True)
            await bot.send_message(uid, f"🏁 Результат: {my_s}:{op_s}\n🏆 Перемога! +{win_net:.2f} 💎")
        else:
            await users_col.update_one({"_id": uid}, {"$inc": {"games_played": 1, "turnover": g['bet']}})
            await bot.send_message(uid, f"🏁 Результат: {my_s}:{op_s}\n❌ Програш.")
    await games_col.update_one({"game_id": g['game_id']}, {"$set": {"status": "finished"}})

# --- ФІНАНСИ ТА ПРОФІЛЬ ---
@dp.message(F.text == "👤 Профіль")
async def profile(m: types.Message):
    u = await get_u(m.from_user.id)
    await m.answer(f"👤 **Профіль**\n\n🆔 ID: `{u['_id']}`\n💰 Баланс: `{u['balance']:.2f} 💎`\n📈 Оборот: `{u['turnover']:.2f} 💎`", parse_mode="Markdown")

@dp.message(F.text == "💎 Баланс")
async def balance(m: types.Message):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📥 Поповнити", callback_data="dep"), InlineKeyboardButton(text="📤 Вивести", callback_data="draw")]])
    await m.answer(f"💰 Баланс: `{u['balance']:.2f} 💎`", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "dep")
async def dep(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(FinanceStates.wait_dep_amount)
    await cb.message.answer("Вкажіть суму поповнення (💎):", reply_markup=cancel_kb())

@dp.message(FinanceStates.wait_dep_amount)
async def dep_amt(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(",", "."))
        await state.update_data(amt=amt)
        await m.answer(f"До сплати: `{amt * CURRATE:.2f} ГРН`\nРеквізити: `5355 2800 2890 2177`\n\nНадішліть фото чеку!", parse_mode="Markdown")
        await state.set_state(FinanceStates.wait_receipt)
    except: await m.answer("Число!")

@dp.message(FinanceStates.wait_receipt, F.photo)
async def dep_rec(m: types.Message, state: FSMContext):
    d = await state.get_data(); tid = str(uuid.uuid4())[:8]
    await finance_col.insert_one({"_id": tid, "uid": m.from_user.id, "amt": d['amt'], "type": "dep"})
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅", callback_data=f"fok_{tid}"), InlineKeyboardButton(text="❌", callback_data=f"fno_{tid}")]])
    await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=f"📥 Поповнення {d['amt']} 💎 від {m.from_user.id}", reply_markup=kb)
    await m.answer("✅ Чек надіслано на перевірку!", reply_markup=main_kb()); await state.clear()

@dp.callback_query(F.data.startswith(("fok_", "fno_")))
async def admin_finance(cb: types.CallbackQuery):
    act, tid = cb.data.split("_")
    f = await finance_col.find_one({"_id": tid})
    if not f: return
    if act == "fok":
        await users_col.update_one({"_id": f['uid']}, {"$inc": {"balance": f['amt']}})
        await bot.send_message(f['uid'], f"✅ Поповнення на {f['amt']} 💎 схвалено!")
    await finance_col.delete_one({"_id": tid})
    await cb.message.edit_caption(caption="✅ Оброблено")

@dp.message(F.text == "❌ Скасувати")
async def cancel(m: types.Message, state: FSMContext):
    await state.clear()
    await m.answer("Дію скасовано.", reply_markup=main_kb())

@dp.message(F.text == "🤝 Рефералка")
async def referal(m: types.Message):
    me = await bot.get_me()
    await m.answer(f"🤝 **Рефералка**\n1% з виграшів друзів!\n\n🔗 Посилання:\n`https://t.me/{me.username}?start={m.from_user.id}`", parse_mode="Markdown")

async def main(): await dp.start_polling(bot)
if __name__ == "__main__": asyncio.run(main())
