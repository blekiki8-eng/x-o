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
CURRATE = 44.50 

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["game_bot_db"] 
users_col = db["users"]
games_col = db["games"]
finance_col = db["finance"]
admin_col = db["admin_data"]

# --- СТАНИ ---
class GameStates(StatesGroup):
    wait_rounds = State()
    wait_bet = State()

class FinanceStates(StatesGroup):
    wait_dep_amount = State()
    wait_receipt = State()
    wait_with_amount = State()
    wait_with_details = State()

# --- КЛАВІАТУРИ ---
def main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="👤 Профіль"), KeyboardButton(text="🎮 Games")],
        [KeyboardButton(text="💎 Баланс"), KeyboardButton(text="🤝 Рефералка")],
        [KeyboardButton(text="⚙️ Налаштування")]
    ], resize_keyboard=True)

def cancel_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Скасувати")]], resize_keyboard=True)

# --- БАЗОВІ ФУНКЦІЇ ---
async def get_u(uid, ref_id=None):
    u = await users_col.find_one({"_id": uid})
    if not u:
        u = {"_id": uid, "balance": 0.0, "referals_count": 0, "turnover": 0.0, "games_played": 0, "referrer": ref_id}
        await users_col.insert_one(u)
        if ref_id and ref_id != uid:
            await users_col.update_one({"_id": ref_id}, {"$inc": {"referals_count": 1}})
    return u

async def add_admin_profit(amt):
    await admin_col.update_one({"_id": "stats"}, {"$inc": {"total_profit": amt}}, upsert=True)

# --- ГЛОБАЛЬНІ ОБРОБНИКИ ---
@dp.message(F.text.in_(["❌ Скасувати", "⬅️ Назад"]))
async def global_cancel(m: types.Message, state: FSMContext):
    await state.clear()
    await m.answer("Скасовано.", reply_markup=main_kb())

# --- ПРОФІЛЬ ---
@dp.message(F.text == "👤 Профіль")
async def profile_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    await m.answer(f"👤 **Профіль**\n\n🆔 ID: `{m.from_user.id}`\n💰 Баланс: `{u['balance']:.2f} 💎`\n📈 Оборот: `{u.get('turnover', 0.0):.2f} 💎`\n🎮 Ігор: `{u.get('games_played', 0)}` 🕹\n👥 Рефералів: `{u.get('referals_count', 0)}` чол.", parse_mode="Markdown")

# --- ФІНАНСИ ---
@dp.message(F.text == "💎 Баланс")
async def balance_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📥 Поповнити", callback_data="dep"), InlineKeyboardButton(text="📤 Вивести", callback_data="with")]])
    await m.answer(f"💰 Баланс: `{u['balance']:.2f} 💎`", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "dep")
async def dep_start(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(FinanceStates.wait_dep_amount)
    await cb.message.answer("💎 Сума (мін. 0.50):", reply_markup=cancel_kb())

@dp.message(FinanceStates.wait_dep_amount)
async def dep_amt(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(",", "."))
        if amt < 0.5: return await m.answer("❌ Мін. 0.50")
        await state.update_data(amt=amt)
        await m.answer(f"💳 До оплати: `{(amt*CURRATE)*1.05:.2f} ГРН`\nРеквізити: `5355 2800 2890 2177`\n**Кидай чек фотографією:**", parse_mode="Markdown")
        await state.set_state(FinanceStates.wait_receipt)
    except: await m.answer("Число!")

@dp.message(FinanceStates.wait_receipt, F.photo)
async def dep_receipt(m: types.Message, state: FSMContext):
    d = await state.get_data()
    tid = str(uuid.uuid4())[:8]
    await finance_col.insert_one({"_id": tid, "uid": m.from_user.id, "amt": d['amt'], "status": "p", "type": "dep"})
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅", callback_data=f"f_ok_{tid}"), InlineKeyboardButton(text="❌", callback_data=f"f_no_{tid}")]])
    await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=f"📥 Поповнення {d['amt']} від `{m.from_user.id}`", reply_markup=kb)
    await m.answer("✅ Чек надіслано!", reply_markup=main_kb())
    await state.clear()

@dp.callback_query(F.data == "with")
async def with_start(cb: types.CallbackQuery, state: FSMContext):
    u = await get_u(cb.from_user.id)
    if u['balance'] < 4: return await cb.answer("❌ Мін. 4 💎", show_alert=True)
    await state.set_state(FinanceStates.wait_with_amount)
    await cb.message.answer("📤 Сума виводу:", reply_markup=cancel_kb())

@dp.message(FinanceStates.wait_with_amount)
async def with_amt(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(",", "."))
        u = await get_u(m.from_user.id)
        if amt > u['balance'] or amt < 4: return await m.answer("❌ Помилка суми!")
        await state.update_data(amt=amt)
        await m.answer("💳 Введіть номер карти:", reply_markup=cancel_kb())
        await state.set_state(FinanceStates.wait_with_details)
    except: await m.answer("Число!")

@dp.message(FinanceStates.wait_with_details)
async def with_final(m: types.Message, state: FSMContext):
    d = await state.get_data()
    await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -d['amt']}})
    await bot.send_message(ADMIN_ID, f"📤 Вивід: `{d['amt']} 💎`\nЮзер: `{m.from_user.id}`\nКарта: `{m.text}`")
    await m.answer("✅ Заявку прийнято!", reply_markup=main_kb())
    await state.clear()

# --- ІГРИ ---
@dp.message(F.text == "🎮 Games")
async def games_menu(m: types.Message):
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Гра Боулінг 🎳"), KeyboardButton(text="Гра Кубик 🎲")], [KeyboardButton(text="⬅️ Назад")]], resize_keyboard=True)
    await m.answer("Обери гру:", reply_markup=kb)

@dp.message(F.text.in_(["Гра Боулінг 🎳", "Гра Кубик 🎲"]))
async def g_rounds(m: types.Message, state: FSMContext):
    await state.update_data(type="🎳" if "Боу" in m.text else "🎲")
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="1 раунд"), KeyboardButton(text="5 раундів")], [KeyboardButton(text="❌ Скасувати")]], resize_keyboard=True)
    await m.answer("Скільки раундів?", reply_markup=kb)
    await state.set_state(GameStates.wait_rounds)

@dp.message(GameStates.wait_rounds, F.text.in_(["1 раунд", "5 раундів"]))
async def g_bet(m: types.Message, state: FSMContext):
    await state.update_data(r=1 if "1" in m.text else 5)
    await m.answer("Ставка (💎):", reply_markup=cancel_kb())
    await state.set_state(GameStates.wait_bet)

@dp.message(GameStates.wait_bet)
async def g_create(m: types.Message, state: FSMContext):
    if m.text == "❌ Скасувати": return
    try:
        bet = float(m.text.replace(",", "."))
        u = await get_u(m.from_user.id)
        if u['balance'] < bet: return await m.answer("❌ Баланс!")
        d = await state.get_data()
        gid = str(uuid.uuid4())[:8]
        await games_col.insert_one({"game_id": gid, "creator_id": m.from_user.id, "opponent_id": None, "type": d['type'], "bet": bet, "rounds_total": d['r'], "status": "waiting", "c_score": [], "o_score": []})
        bot_u = await bot.get_me()
        await m.answer(f"✅ Готово! Посилання:\n`https://t.me/{bot_u.username}?start=game_{gid}`", reply_markup=main_kb())
        await state.clear()
    except: await m.answer("Число!")

@dp.message(F.dice)
async def handle_dice(m: types.Message):
    g = await games_col.find_one({"status": "playing", "turn": m.from_user.id})
    if not g or m.dice.emoji != g['type']: return
    is_c = m.from_user.id == g['creator_id']
    opp = g['opponent_id'] if is_c else g['creator_id']
    f = "c_score" if is_c else "o_score"
    await games_col.update_one({"game_id": g['game_id']}, {"$push": {f: m.dice.value}})
    await m.forward(opp)
    await bot.send_message(opp, f"Суперник вибив: {m.dice.value}. Твій хід `{g['type']}`!")
    upd = await games_col.find_one({"game_id": g['game_id']})
    if len(upd['c_score']) == upd['rounds_total'] and len(upd['o_score']) == upd['rounds_total']:
        await finish_game(upd)
    else:
        await games_col.update_one({"game_id": g['game_id']}, {"$set": {"turn": opp}})

async def finish_game(g):
    s1, s2 = sum(g['c_score']), sum(g['o_score'])
    await add_admin_profit(g['bet'] * 0.01)
    if s1 == s2:
        await users_col.update_many({"_id": {"$in": [g['creator_id'], g['opponent_id']]}}, {"$inc": {"balance": g['bet']}})
        for u in [g['creator_id'], g['opponent_id']]: await bot.send_message(u, "🤝 Нічия!")
    else:
        win = g['creator_id'] if s1 > s2 else g['opponent_id']
        lose = g['opponent_id'] if s1 > s2 else g['creator_id']
        await users_col.update_one({"_id": win}, {"$inc": {"balance": g['bet']*1.98, "turnover": g['bet'], "games_played": 1}})
        await users_col.update_one({"_id": lose}, {"$inc": {"games_played": 1}})
        uw = await users_col.find_one({"_id": win})
        if uw.get("referrer"):
            await users_col.update_one({"_id": uw['referrer']}, {"$inc": {"balance": g['bet']*0.01}})
            try: await bot.send_message(uw['referrer'], f"💰 Реферал виграв! Вам +{g['bet']*0.01:.2f}")
            except: pass
        await bot.send_message(win, f"🏆 +{g['bet']*1.98:.2f} 💎"); await bot.send_message(lose, "❌ Програш")
    await games_col.update_one({"game_id": g['game_id']}, {"$set": {"status": "finished"}})

# --- АДМІНКА ТА СТАРТ ---
@dp.callback_query(F.data == "admin_panel")
async def adm_p(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID: return
    t_t = await users_col.find().sort("turnover", -1).limit(10).to_list(10)
    t_r = await users_col.find().sort("referals_count", -1).limit(10).to_list(10)
    st = await admin_col.find_one({"_id": "stats"})
    p = st.get("total_profit", 0) if st else 0
    txt = f"👨‍💻 **Адмін**\n💰 Каса 1%: `{p:.2f} 💎`\n\n🏆 **Топ Оборот:**\n"
    for i, u in enumerate(t_t, 1): txt += f"{i}. `{u['_id']}` - {u.get('turnover',0):.1f}\n"
    txt += "\n👥 **Топ Рефи:**\n"
    for i, u in enumerate(t_r, 1): txt += f"{i}. `{u['_id']}` - {u.get('referals_count',0)}\n"
    await cb.message.answer(txt, parse_mode="Markdown")

@dp.message(F.text == "🤝 Рефералка")
async def ref_p(m: types.Message):
    u = await get_u(m.from_user.id)
    me = await bot.get_me()
    await m.answer(f"🤝 **Рефералка**\n🔗 `https://t.me/{me.username}?start={m.from_user.id}`\n\n👥 Рефералів: {u['referals_count']}\n🎁 1% від виграшу друзів!")

@dp.message(Command("start"))
async def start(m: types.Message, state: FSMContext):
    await state.clear()
    a = m.text.split()
    rid = None
    if len(a) > 1:
        if a[1].startswith("game_"):
            g = await games_col.find_one({"game_id": a[1][5:], "status": "waiting"})
            if g and g['creator_id'] != m.from_user.id:
                u = await get_u(m.from_user.id)
                if u['balance'] >= g['bet']:
                    await users_col.update_many({"_id": {"$in": [g['creator_id'], m.from_user.id]}}, {"$inc": {"balance": -g['bet']}})
                    await games_col.update_one({"game_id": g['game_id']}, {"$set": {"opponent_id": m.from_user.id, "status": "playing", "turn": g['creator_id']}})
                    await bot.send_message(g['creator_id'], f"🎮 Почали! Твій хід `{g['type']}`")
                    return await m.answer("🎮 Гра почалася!")
        elif a[1].isdigit(): rid = int(a[1])
    await get_u(m.from_user.id, rid)
    await m.answer("💎 Привіт!", reply_markup=main_kb())

@dp.message(F.text == "⚙️ Налаштування")
async def set_p(m: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🆘 Підтримка", url="https://t.me/vex0o0")], [InlineKeyboardButton(text="📊 Адмін Панель", callback_data="admin_panel")]])
    await m.answer("⚙️ Налаштування:", reply_markup=kb)

async def main(): await dp.start_polling(bot)
if __name__ == "__main__": asyncio.run(main())
