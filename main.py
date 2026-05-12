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

# --- НАЛАШТУВАННЯ СЕРЕДОВИЩА ---
load_dotenv()
API_TOKEN = os.getenv("BOT_TOKEN") 
MONGO_URL = os.getenv("MONGO_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CURRATE = 44.50  # Курс грн за 1 💎

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- БАЗА ДАНИХ ---
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

# --- ГОЛОВНЕ МЕНЮ ---
def main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="👤 Профіль"), KeyboardButton(text="🎮 Games")],
        [KeyboardButton(text="💎 Баланс"), KeyboardButton(text="🤝 Рефералка")],
        [KeyboardButton(text="⚙️ Налаштування")]
    ], resize_keyboard=True)

def cancel_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Скасувати")]], resize_keyboard=True)

# --- ДОПОМІЖНІ ФУНКЦІЇ ---
async def get_u(uid, ref_id=None):
    u = await users_col.find_one({"_id": uid})
    if not u:
        u = {"_id": uid, "balance": 0.0, "referals_count": 0, "turnover": 0.0, "referrer": ref_id}
        await users_col.insert_one(u)
        if ref_id and ref_id != uid:
            await users_col.update_one({"_id": ref_id}, {"$inc": {"referals_count": 1}})
    return u

# --- ЛОГІКА АДМІН-ПАНЕЛІ ---
async def get_admin_stats_text():
    stats = await stats_col.find_one({"_id": "global"})
    admin_bal = stats.get("admin_balance", 0.0) if stats else 0.0
    top_users = users_col.find().sort("turnover", -1).limit(10)
    
    text = f"👑 **АДМІНІСТРАТИВНА ПАНЕЛЬ**\n\n"
    text += f"💰 **Баланс адмін-панелі (Каса):** `{admin_bal:.2f} 💎`\n"
    text += "────────────────\n"
    text += "🏆 **ТОП-10 ГРАВЦІВ ЗА ОБОРОТОМ:**\n"
    
    i = 1
    async for user in top_users:
        text += f"{i}. ID: `{user['_id']}` | 💸 Оборот: `{user.get('turnover', 0):.2f}` | 👥 Реф: {user.get('referals_count', 0)}\n"
        i += 1
    return text

# --- ОБРОБНИКИ КНОПОК ---
@dp.message(F.text == "⚙️ Налаштування")
async def settings_handler(m: types.Message):
    kb_list = [[InlineKeyboardButton(text="🆘 Підтримка", url="https://t.me/vex0o0")]]
    
    # Тільки для адміна додаємо вхід у панель
    if m.from_user.id == ADMIN_ID:
        kb_list.append([InlineKeyboardButton(text="👑 Адмін Панель", callback_data="open_admin_panel")])
    
    kb = InlineKeyboardMarkup(inline_keyboard=kb_list)
    await m.answer("⚙️ **Налаштування**\n\nТут ви можете знайти контакти підтримки або відкрити панель керування.", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "open_admin_panel")
async def admin_callback(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID: return
    text = await get_admin_stats_text()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_settings")]])
    await cb.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)

@dp.callback_query(F.data == "back_to_settings")
async def back_to_settings(cb: types.CallbackQuery):
    kb_list = [[InlineKeyboardButton(text="🆘 Підтримка", url="https://t.me/vex0o0")]]
    if cb.from_user.id == ADMIN_ID:
        kb_list.append([InlineKeyboardButton(text="👑 Адмін Панель", callback_data="open_admin_panel")])
    await cb.message.edit_text("⚙️ **Налаштування**", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_list))

# --- ГРА ТА DICE ---
@dp.message(Command("start"))
async def cmd_start(m: types.Message, state: FSMContext):
    await state.clear()
    args = m.text.split()
    u = await get_u(m.from_user.id, int(args[1]) if len(args) > 1 and args[1].isdigit() else None)
    
    if len(args) > 1 and args[1].startswith("game_"):
        gid = args[1].replace("game_", "")
        g = await games_col.find_one({"game_id": gid, "status": "waiting"})
        if g and g['creator_id'] != m.from_user.id and u['balance'] >= g['bet']:
            await users_col.update_many({"_id": {"$in": [g['creator_id'], m.from_user.id]}}, {"$inc": {"balance": -g['bet']}})
            await games_col.update_one({"game_id": gid}, {"$set": {"opponent_id": m.from_user.id, "status": "playing", "turn": g['creator_id']}})
            await bot.send_message(g['creator_id'], f"🎮 Гравець приєднався! Твій хід: `{g['type']}`")
            return await m.answer("🎮 Ви приєдналися! Чекайте хід суперника.", reply_markup=main_kb())
    await m.answer("💎 Ласкаво просимо!", reply_markup=main_kb())

@dp.message(F.text == "🎮 Games")
async def games_menu(m: types.Message):
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🎳 Боулінг"), KeyboardButton(text="🎲 Кубик")], [KeyboardButton(text="❌ Скасувати")]], resize_keyboard=True)
    await m.answer("Оберіть гру:", reply_markup=kb)

@dp.message(F.text.in_(["🎳 Боулінг", "🎲 Кубик"]))
async def g_mode(m: types.Message, state: FSMContext):
    await state.update_data(type="🎳" if "🎳" in m.text else "🎲")
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="1"), KeyboardButton(text="5")], [KeyboardButton(text="❌ Скасувати")]], resize_keyboard=True)
    await m.answer("Кількість раундів:", reply_markup=kb)
    await state.set_state(GameStates.wait_rounds)

@dp.message(GameStates.wait_rounds, F.text.in_(["1", "5"]))
async def g_rounds(m: types.Message, state: FSMContext):
    await state.update_data(r=int(m.text))
    await m.answer("Введіть ставку 💎:", reply_markup=cancel_kb())
    await state.set_state(GameStates.wait_bet)

@dp.message(GameStates.wait_bet)
async def g_create(m: types.Message, state: FSMContext):
    if m.text == "❌ Скасувати": 
        await state.clear()
        return await m.answer("Скасовано.", reply_markup=main_kb())
    try:
        bet = float(m.text.replace(",", "."))
        u = await get_u(m.from_user.id); d = await state.get_data()
        if u['balance'] < bet: return await m.answer("❌ Мало коштів.")
        gid = str(uuid.uuid4())[:8]
        await games_col.insert_one({"game_id": gid, "creator_id": m.from_user.id, "status": "waiting", "type": d['type'], "bet": bet, "rounds_total": d['r'], "c_score": [], "o_score": [], "opponent_id": None})
        me = await bot.get_me()
        await m.answer(f"✅ Гра створена!\n`https://t.me/{me.username}?start=game_{gid}`", reply_markup=main_kb())
        await state.clear()
    except: await m.answer("Введіть число!")

@dp.message(F.dice)
async def dice_handler(m: types.Message):
    g = await games_col.find_one({"status": "playing", "turn": m.from_user.id})
    if not g or m.dice.emoji != g['type']: return
    opp_id = g['opponent_id'] if m.from_user.id == g['creator_id'] else g['creator_id']
    f = "c_score" if m.from_user.id == g['creator_id'] else "o_score"
    await bot.send_message(opp_id, f"Суперник кинув `{g['type']}`")
    await m.forward(opp_id)
    await asyncio.sleep(4)
    await games_col.update_one({"game_id": g['game_id']}, {"$push": {f: m.dice.value}})
    upd = await games_col.find_one({"game_id": g['game_id']})
    
    if len(upd['c_score']) == upd['rounds_total'] and len(upd['o_score']) == upd['rounds_total']:
        s1, s2 = sum(upd['c_score']), sum(upd['o_score'])
        for uid in [upd['creator_id'], upd['opponent_id']]:
            my_s, op_s = (s1, s2) if uid == upd['creator_id'] else (s2, s1)
            u = await users_col.find_one({"_id": uid})
            if s1 == s2:
                await users_col.update_one({"_id": uid}, {"$inc": {"balance": upd['bet']}})
                await bot.send_message(uid, f"🏁 {my_s}:{op_s} — Нічия!")
            elif my_s > op_s:
                fee = 0.01 if u.get("referrer") else 0.02
                if u.get("referrer"): await users_col.update_one({"_id": u['referrer']}, {"$inc": {"balance": (upd['bet']*2)*0.01}})
                win = (upd['bet']*2) - (upd['bet']*2*fee)
                await users_col.update_one({"_id": uid}, {"$inc": {"balance": win, "turnover": upd['bet']}})
                # Гроші капають адміну в панель
                await stats_col.update_one({"_id": "global"}, {"$inc": {"admin_balance": (upd['bet']*2*fee)}}, upsert=True)
                await bot.send_message(uid, f"🏁 {my_s}:{op_s} — Перемога! +{win:.2f} 💎")
            else:
                await users_col.update_one({"_id": uid}, {"$inc": {"turnover": upd['bet']}})
                await bot.send_message(uid, f"🏁 {my_s}:{op_s} — Програш.")
        await games_col.update_one({"game_id": upd['game_id']}, {"$set": {"status": "finished"}})
    else:
        await games_col.update_one({"game_id": g['game_id']}, {"$set": {"turn": opp_id}})
        await bot.send_message(opp_id, f"Твій хід! Тисни `{g['type']}`")

# --- ФІНАНСИ ---
@dp.message(F.text == "👤 Профіль")
async def profile(m: types.Message):
    u = await get_u(m.from_user.id)
    await m.answer(f"👤 **ID:** `{u['_id']}`\n💰 **Баланс:** `{u['balance']:.2f} 💎`", parse_mode="Markdown")

@dp.message(F.text == "💎 Баланс")
async def balance(m: types.Message):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📥 Поповнити", callback_data="dep")]])
    await m.answer(f"💰 Ваш баланс: `{u['balance']:.2f} 💎`", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "dep")
async def dep(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(FinanceStates.wait_dep_amount)
    await cb.message.answer("Введіть суму поповнення (💎):", reply_markup=cancel_kb())

@dp.message(FinanceStates.wait_dep_amount)
async def dep_amt(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text)
        await state.update_data(amt=amt)
        await m.answer(f"Сплата: {amt*CURRATE} ГРН. Надішліть фото чеку.")
        await state.set_state(FinanceStates.wait_receipt)
    except: await m.answer("Помилка! Введіть число.")

@dp.message(FinanceStates.wait_receipt, F.photo)
async def dep_rec(m: types.Message, state: FSMContext):
    d = await state.get_data(); tid = str(uuid.uuid4())[:8]
    await finance_col.insert_one({"_id": tid, "uid": m.from_user.id, "amt": d['amt']})
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Схвалити", callback_data=f"fok_{tid}")]])
    await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=f"Депозит {d['amt']} 💎 від {m.from_user.id}", reply_markup=kb)
    await m.answer("Чек на перевірці!", reply_markup=main_kb()); await state.clear()

@dp.callback_query(F.data.startswith("fok_"))
async def admin_f(cb: types.CallbackQuery):
    tid = cb.data.split("_")[1]
    f = await finance_col.find_one({"_id": tid})
    await users_col.update_one({"_id": f['uid']}, {"$inc": {"balance": f['amt']}})
    await bot.send_message(f['uid'], f"✅ Баланс поповнено на {f['amt']} 💎!")
    await finance_col.delete_one({"_id": tid})
    await cb.message.edit_caption(caption="✅ Схвалено")

@dp.message(F.text == "🤝 Рефералка")
async def referal(m: types.Message):
    me = await bot.get_me()
    await m.answer(f"🤝 1% з виграшів друзів!\n`https://t.me/{me.username}?start={m.from_user.id}`")

@dp.message(F.text == "❌ Скасувати")
async def cancel(m: types.Message, state: FSMContext):
    await state.clear()
    await m.answer("Дію скасовано.", reply_markup=main_kb())

async def main(): await dp.start_polling(bot)
if __name__ == "__main__": asyncio.run(main())
