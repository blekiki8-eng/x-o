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
stats_col = db["stats"]

# --- СТАНИ ---
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

# --- СТАРТ ТА ПРИЄДНАННЯ ---
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
        
        if not g: 
            return await m.answer("❌ Гра не знайдена або вже зайнята.", reply_markup=main_kb())
        if g['creator_id'] == user_id: 
            return await m.answer("❌ Ви не можете грати самі з собою.")
        if u['balance'] < g['bet']: 
            return await m.answer(f"❌ Недостатньо коштів. Ставка: {g['bet']} 💎")
        
        # Списання ставок та активація гри
        await users_col.update_many({"_id": {"$in": [g['creator_id'], user_id]}}, {"$inc": {"balance": -g['bet']}})
        await games_col.update_one({"game_id": gid}, {"$set": {"opponent_id": user_id, "status": "playing", "turn": g['creator_id']}})
        
        # Повідомлення творцю (його хід перший)
        await bot.send_message(g['creator_id'], f"🎮 Гравець приєднався! Твій хід: `{g['type']}`", parse_mode="Markdown")
        # Повідомлення приєднаному
        return await m.answer(f"🎮 Ви приєдналися! Очікуйте хід суперника: `{g['type']}`", reply_markup=main_kb(), parse_mode="Markdown")
    
    await m.answer("💎 Вітаємо в Different Games!", reply_markup=main_kb())

# --- СТВОРЕННЯ ГРИ ---
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
    await m.answer(f"Раундів: {m.text}. Вкажіть суму ставки (мін. 0.07 💎):", reply_markup=cancel_kb())
    await state.set_state(GameStates.wait_bet)

@dp.message(GameStates.wait_bet)
async def g_create(m: types.Message, state: FSMContext):
    if m.text == "❌ Скасувати":
        await state.clear()
        return await m.answer("Скасовано.", reply_markup=main_kb())
    try:
        bet = float(m.text.replace(",", "."))
        if bet < 0.07: return await m.answer("❌ Мінімум 0.07 💎")
        u = await get_u(m.from_user.id); d = await state.get_data()
        if u['balance'] < bet: return await m.answer(f"❌ Недостатньо 💎. Баланс: {u['balance']:.2f}")
        
        gid = str(uuid.uuid4())[:8]
        await games_col.insert_one({
            "game_id": gid, "creator_id": m.from_user.id, "status": "waiting", 
            "type": d['type'], "bet": bet, "rounds_total": d['r'], 
            "c_score": [], "o_score": [], "opponent_id": None
        })
        
        me = await bot.get_me()
        link = f"https://t.me/{me.username}?start=game_{gid}"
        await m.answer(f"✅ Гра створена!\nСтавка: {bet} 💎\n\n🔗 Надішліть посилання другу:\n`{link}`", reply_markup=main_kb(), parse_mode="Markdown")
        await state.clear()
    except ValueError:
        await m.answer("⚠️ Введіть число (суму ставки).")

# --- ПРОЦЕС ГРИ ---
@dp.message(F.dice)
async def dice_handler(m: types.Message):
    g = await games_col.find_one({"status": "playing", "turn": m.from_user.id})
    if not g or m.dice.emoji != g['type']: return

    opp_id = g['opponent_id'] if m.from_user.id == g['creator_id'] else g['creator_id']
    field = "c_score" if m.from_user.id == g['creator_id'] else "o_score"
    
    await bot.send_message(opp_id, f"Суперник кинув `{g['type']}`:")
    await m.forward(opp_id)
    
    await asyncio.sleep(4) # Чекаємо на зупинку анімації кубика
    await games_col.update_one({"game_id": g['game_id']}, {"$push": {field: m.dice.value}})
    
    upd = await games_col.find_one({"game_id": g['game_id']})
    
    if len(upd['c_score']) == upd['rounds_total'] and len(upd['o_score']) == upd['rounds_total']:
        await finish_game(upd)
    else:
        await games_col.update_one({"game_id": g['game_id']}, {"$set": {"turn": opp_id}})
        await m.answer(f"Випало: {m.dice.value}. Тепер хід суперника.")
        await bot.send_message(opp_id, f"Твій хід! Тисни на емодзі: `{g['type']}`", parse_mode="Markdown")

async def finish_game(g):
    s1, s2 = sum(g['c_score']), sum(g['o_score'])
    for uid in [g['creator_id'], g['opponent_id']]:
        my_s, op_s = (s1, s2) if uid == g['creator_id'] else (s2, s1)
        res_text = f"🏁 Гра завершена!\nВи: {my_s} | Суперник: {op_s}\n\n"
        
        if s1 == s2:
            await users_col.update_one({"_id": uid}, {"$inc": {"balance": g['bet']}})
            await bot.send_message(uid, res_text + "🤝 Нічия! Ставку повернуто.")
        elif my_s > op_s:
            raw_win = g['bet'] * 2
            u = await users_col.find_one({"_id": uid})
            fee = 0.02 # 2% стандарт
            if u.get("referrer"):
                await users_col.update_one({"_id": u['referrer']}, {"$inc": {"balance": raw_win * 0.01}})
                fee = 0.01 # 1% якщо є реферал
            win_net = raw_win - (raw_win * fee)
            await users_col.update_one({"_id": uid}, {"$inc": {"balance": win_net, "turnover": g['bet'], "games_played": 1}})
            await bot.send_message(uid, res_text + f"🏆 Перемога! +{win_net:.2f} 💎")
        else:
            await users_col.update_one({"_id": uid}, {"$inc": {"games_played": 1}})
            await bot.send_message(uid, res_text + "❌ Програш.")
            
    await games_col.update_one({"game_id": g['game_id']}, {"$set": {"status": "finished"}})

# --- ПРОФІЛЬ ТА ФІНАНСИ ---
@dp.message(F.text == "👤 Профіль")
async def profile_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    await m.answer(f"👤 **Профіль**\n🆔 ID: `{u['_id']}`\n💰 Баланс: `{u['balance']:.2f} 💎`", parse_mode="Markdown")

@dp.message(F.text == "🤝 Рефералка")
async def referal_cmd(m: types.Message):
    me = await bot.get_me()
    await m.answer(f"🤝 **Рефералка**\nОтримуйте 1% від виграшів друзів!\n\n🔗 Посилання:\n`https://t.me/{me.username}?start={m.from_user.id}`", parse_mode="Markdown")

@dp.message(F.text == "💎 Баланс")
async def balance_menu(m: types.Message):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📥 Поповнити", callback_data="dep"), InlineKeyboardButton(text="📤 Вивести", callback_data="draw")]])
    await m.answer(f"💰 Баланс: `{u['balance']:.2f} 💎`", reply_markup=kb, parse_mode="Markdown")

# --- ПОПОВНЕННЯ / ВИВЕДЕННЯ ---
@dp.callback_query(F.data == "dep")
async def dep_s(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(FinanceStates.wait_dep_amount)
    await cb.message.answer("💎 Сума поповнення (мін. 0.50):", reply_markup=cancel_kb())

@dp.message(FinanceStates.wait_dep_amount)
async def dep_a(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(",", "."))
        if amt < 0.50: return await m.answer("❌ Мінімум 0.50 💎")
        await state.update_data(amt=amt)
        pay = (amt * CURRATE) * 1.05
        await m.answer(f"До сплати: {pay:.2f} ГРН\nРеквізити: `5355 2800 2890 2177`\n\nНадішліть фото чеку!")
        await state.set_state(FinanceStates.wait_receipt)
    except: await m.answer("Введіть число!")

@dp.message(FinanceStates.wait_receipt, F.photo)
async def dep_r(m: types.Message, state: FSMContext):
    d = await state.get_data(); tid = str(uuid.uuid4())[:8]
    await finance_col.insert_one({"_id": tid, "uid": m.from_user.id, "amt": d['amt'], "type": "dep"})
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅", callback_data=f"fok_{tid}"), InlineKeyboardButton(text="❌", callback_data=f"fno_{tid}")]])
    await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=f"📥 Деп {d['amt']} від {m.from_user.id}", reply_markup=kb)
    await m.answer("✅ Чек надіслано!", reply_markup=main_kb()); await state.clear()

@dp.callback_query(F.data == "draw")
async def draw_s(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(FinanceStates.wait_draw_sum)
    await cb.message.answer("💎 Сума виводу (мін. 4.50):", reply_markup=cancel_kb())

@dp.message(FinanceStates.wait_draw_sum)
async def draw_1(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(",", "."))
        u = await get_u(m.from_user.id)
        if amt < 4.50: return await m.answer("❌ Мінімум 4.50 💎")
        if u['balance'] < amt: return await m.answer("❌ Мало коштів")
        await state.update_data(amt=amt)
        await m.answer("Вкажіть реквізити (Карта, ПІБ):")
        await state.set_state(FinanceStates.wait_draw_details)
    except: await m.answer("Введіть число!")

@dp.message(FinanceStates.wait_draw_details)
async def draw_2(m: types.Message, state: FSMContext):
    d = await state.get_data(); tid = str(uuid.uuid4())[:8]
    await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -d['amt']}})
    await finance_col.insert_one({"_id": tid, "uid": m.from_user.id, "amt": d['amt'], "type": "draw", "info": m.text})
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅", callback_data=f"dok_{tid}"), InlineKeyboardButton(text="❌", callback_data=f"dno_{tid}")]])
    await bot.send_message(ADMIN_ID, f"📤 ВИВІД {d['amt']} 💎\nID: {m.from_user.id}\nДані: {m.text}", reply_markup=kb)
    await m.answer("✅ Заявку створено!", reply_markup=main_kb()); await state.clear()

# --- АДМІН-ОБРОБКА ---
@dp.callback_query(F.data.startswith(("fok_", "fno_", "dok_", "dno_")))
async def admin_fin(cb: types.CallbackQuery):
    act, tid = cb.data.split("_")
    f = await finance_col.find_one({"_id": tid})
    if not f: return
    if act == "fok":
        await users_col.update_one({"_id": f['uid']}, {"$inc": {"balance": f['amt']}})
        await bot.send_message(f['uid'], f"✅ Поповнення на {f['amt']} 💎 схвалено!")
    elif act == "dok":
        await bot.send_message(f['uid'], f"✅ Виплата {f['amt']} 💎 виконана!")
    elif act == "dno":
        await users_col.update_one({"_id": f['uid']}, {"$inc": {"balance": f['amt']}})
        await bot.send_message(f['uid'], "❌ Вивід відхилено. Кошти повернуто.")
    await finance_col.delete_one({"_id": tid})
    await cb.message.edit_reply_markup(reply_markup=None)

@dp.message(F.text == "❌ Скасувати")
async def global_cancel(m: types.Message, state: FSMContext):
    await state.clear()
    await m.answer("Скасовано.", reply_markup=main_kb())

async def main(): await dp.start_polling(bot)
if __name__ == "__main__": asyncio.run(main())
