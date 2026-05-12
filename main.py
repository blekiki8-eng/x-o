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

# --- НАЛАШТУВАННЯ ---
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

# --- СТАНИ FSM ---
class GameStates(StatesGroup):
    wait_rounds = State()
    wait_bet = State()

class FinanceStates(StatesGroup):
    wait_dep_amount = State()
    wait_receipt = State()
    wait_draw_sum = State()
    wait_draw_iban = State()
    wait_draw_pib = State()
    wait_draw_ipn = State()

# --- КЛАВІАТУРИ ---
def main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="👤 Профіль"), KeyboardButton(text="🎮 Games")],
        [KeyboardButton(text="💎 Баланс"), KeyboardButton(text="🤝 Рефералка")],
        [KeyboardButton(text="🏆 Топ Рефералів"), KeyboardButton(text="📊 Топ Оборотів")],
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

# --- ГЛОБАЛЬНЕ СКАСУВАННЯ ---
@dp.message(F.text == "❌ Скасувати")
async def global_cancel(m: types.Message, state: FSMContext):
    await state.clear()
    await m.answer("Дію скасовано. Повернення в головне меню.", reply_markup=main_kb())

# --- КОМАНДА /START ТА ПРИЄДНАННЯ ---
@dp.message(Command("start"))
async def cmd_start(m: types.Message, state: FSMContext):
    await state.clear()
    args = m.text.split()
    user_id = m.from_user.id
    ref_id = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
    u = await get_u(user_id, ref_id)
    
    if len(args) > 1 and args[1].startswith("game_"):
        gid = args[1].replace("game_", "")
        g = await games_col.find_one({"game_id": gid, "status": "waiting"})
        if not g: return await m.answer("❌ Гра вже недійсна або завершена.", reply_markup=main_kb())
        if g['creator_id'] == user_id: return await m.answer("❌ Ви не можете грати самі з собою.", reply_markup=main_kb())
        if u['balance'] < g['bet']: return await m.answer(f"❌ Необхідно {g['bet']} 💎 для входу.", reply_markup=main_kb())
        
        await users_col.update_many({"_id": {"$in": [g['creator_id'], user_id]}}, {"$inc": {"balance": -g['bet']}})
        await games_col.update_one({"game_id": gid}, {"$set": {"opponent_id": user_id, "status": "playing", "turn": g['creator_id']}})
        
        await bot.send_message(g['creator_id'], f"🎮 Гравець приєднався!\n\n**Ваш хід** `{g['type']}`", parse_mode="Markdown")
        return await m.answer(f"🎮 Ви приєдналися!\n\n**Очікуйте хід суперника** `{g['type']}`", parse_mode="Markdown", reply_markup=main_kb())
    await m.answer("💎 Ласкаво просимо до Different Games!", reply_markup=main_kb())

# --- ПРОФІЛЬ ---
@dp.message(F.text == "👤 Профіль")
async def profile_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    await m.answer(
        f"👤 **Мій Профіль**\n\n🆔 ID: `{u['_id']}`\n💰 Баланс: `{u['balance']:.2f} 💎`"
        f"\n📈 Оборот: `{u.get('turnover', 0.0):.2f} 💎`"
        f"\n👥 Запрошено друзів: `{u.get('referals_count', 0)}`"
        f"\n🎮 Ігор зіграно: `{u.get('games_played', 0)}`", parse_mode="Markdown"
    )

# --- ТОПИ ---
@dp.message(F.text == "🏆 Топ Рефералів")
async def top_refs(m: types.Message):
    top = await users_col.find().sort("referals_count", -1).limit(10).to_list(10)
    text = "🏆 **Топ 10 по рефералах:**\n\n"
    for i, u in enumerate(top, 1):
        text += f"{i}. ID: `{u['_id']}` — {u.get('referals_count', 0)} друзів\n"
    await m.answer(text, parse_mode="Markdown")

@dp.message(F.text == "📊 Топ Оборотів")
async def top_turnover(m: types.Message):
    top = await users_col.find().sort("turnover", -1).limit(10).to_list(10)
    text = "📊 **Топ 10 по обороту:**\n\n"
    for i, u in enumerate(top, 1):
        text += f"{i}. ID: `{u['_id']}` — {u.get('turnover', 0.0):.2f} 💎\n"
    await m.answer(text, parse_mode="Markdown")

# --- РЕФЕРАЛКА ---
@dp.message(F.text == "🤝 Рефералка")
async def referal_cmd(m: types.Message):
    me = await bot.get_me()
    await m.answer(
        "🤝 **Вітаю у Рефералка**\n\nСуть: коли запрошений друг виграє, вам нараховується **+0.01%** від його виграшу.\n\n"
        f"🔗 Ваше посилання:\n`https://t.me/{me.username}?start={m.from_user.id}`", parse_mode="Markdown"
    )

# --- GAMES & DICE LOGIC ---
@dp.message(F.text == "🎮 Games")
async def games_menu(m: types.Message):
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🎳 Боулінг"), KeyboardButton(text="🎲 Кубик")], [KeyboardButton(text="❌ Скасувати")]], resize_keyboard=True)
    await m.answer("Оберіть режим гри:", reply_markup=kb)

@dp.message(F.text.in_(["🎳 Боулінг", "🎲 Кубик"]))
async def g_mode(m: types.Message, state: FSMContext):
    await state.update_data(type="🎳" if "🎳" in m.text else "🎲")
    await m.answer("Кількість раундів (1 або 5):", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="1"), KeyboardButton(text="5")], [KeyboardButton(text="❌ Скасувати")]], resize_keyboard=True))
    await state.set_state(GameStates.wait_rounds)

@dp.message(GameStates.wait_rounds, F.text.in_(["1", "5"]))
async def g_rounds(m: types.Message, state: FSMContext):
    await state.update_data(r=int(m.text))
    await m.answer("Введіть ставку (мін. 0.07 💎):", reply_markup=cancel_kb())
    await state.set_state(GameStates.wait_bet)

@dp.message(GameStates.wait_bet)
async def g_create(m: types.Message, state: FSMContext):
    try:
        bet = float(m.text.replace(",", "."))
        if bet < 0.07: return await m.answer("❌ Мінімальна ставка 0.07 💎")
        u = await get_u(m.from_user.id)
        if u['balance'] < bet: return await m.answer("❌ Недостатньо балансу!")
        
        d = await state.get_data()
        gid = str(uuid.uuid4())[:8]
        await games_col.insert_one({"game_id": gid, "creator_id": m.from_user.id, "status": "waiting", "type": d['type'], "bet": bet, "rounds_total": d['r'], "c_score": [], "o_score": []})
        me = await bot.get_me()
        await m.answer(f"✅ Гра створена! Ваш хід `{d['type']}`\n\n🔗 Посилання для опонента:\n`https://t.me/{me.username}?start=game_{gid}`", parse_mode="Markdown", reply_markup=main_kb())
        await state.clear()
    except: await m.answer("Введіть коректне число!")

@dp.message(F.dice)
async def dice_handler(m: types.Message):
    g = await games_col.find_one({"status": "playing", "turn": m.from_user.id})
    if not g or m.dice.emoji != g['type']: return
    
    is_c = (m.from_user.id == g['creator_id'])
    opp = g['opponent_id'] if is_c else g['creator_id']
    f = "c_score" if is_c else "o_score"
    
    await bot.send_message(opp, "Суперник кинув стікер:")
    await m.forward(opp)
    
    await asyncio.sleep(4) 
    val = m.dice.value
    await m.answer(f"Результат кидка: {val}")
    await bot.send_message(opp, f"У суперника випало: {val}")
    
    await games_col.update_one({"game_id": g['game_id']}, {"$push": {f: val}})
    upd = await games_col.find_one({"game_id": g['game_id']})
    
    if len(upd['c_score']) == upd['rounds_total'] and len(upd['o_score']) == upd['rounds_total']:
        await finish_game(upd)
    else:
        await games_col.update_one({"game_id": g['game_id']}, {"$set": {"turn": opp}})
        await m.answer("Тепер хід суперника ⏳")
        await bot.send_message(opp, f"Ваш хід! Кидайте `{g['type']}`", parse_mode="Markdown")

async def finish_game(g):
    s1, s2 = sum(g['c_score']), sum(g['o_score'])
    for uid in [g['creator_id'], g['opponent_id']]:
        my_s, opp_s = (s1, s2) if uid == g['creator_id'] else (s2, s1)
        res = f"📊 **Результат гри**\n\nУ вас: `{my_s}`\nУ противника: `{opp_s}`\n\n"
        if s1 == s2:
            await users_col.update_one({"_id": uid}, {"$inc": {"balance": g['bet']}})
            await bot.send_message(uid, res + "🤝 Нічия! Ставки повернуті.")
        else:
            win = (uid == g['creator_id'] and s1 > s2) or (uid == g['opponent_id'] and s2 > s1)
            if win:
                amt = g['bet'] * 1.98
                await users_col.update_one({"_id": uid}, {"$inc": {"balance": amt, "turnover": g['bet'], "games_played": 1}})
                u = await users_col.find_one({"_id": uid})
                if u.get("referrer"): await users_col.update_one({"_id": u['referrer']}, {"$inc": {"balance": amt * 0.0001}})
                await bot.send_message(uid, res + f"🏆 Вітаю ви виграли +{amt:.2f} 💎")
            else:
                await users_col.update_one({"_id": uid}, {"$inc": {"games_played": 1}})
                await bot.send_message(uid, res + "❌ Нажаль ви програли")
    await games_col.update_one({"game_id": g['game_id']}, {"$set": {"status": "finished"}})

# --- БАЛАНС / ПОПОВНЕННЯ / ВИВІД ---
@dp.message(F.text == "💎 Баланс")
async def balance_menu(m: types.Message):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📥 Поповнити", callback_data="dep"), InlineKeyboardButton(text="📤 Вивести", callback_data="draw")]])
    await m.answer(f"💰 Ваш баланс: `{u['balance']:.2f} 💎`", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "dep")
async def dep_s(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(FinanceStates.wait_dep_amount)
    await cb.message.answer("💎 Введіть суму (мін. 0.50):", reply_markup=cancel_kb())

@dp.message(FinanceStates.wait_dep_amount)
async def dep_a(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text)
        if amt < 0.50: return await m.answer("❌ Мінімум 0.50 💎")
        await state.update_data(amt=amt)
        await m.answer(f"До оплати: `{(amt*CURRATE)*1.05:.2f} ГРН`\nРеквізити: `5355 2800 2890 2177`\n\nНадішліть фото чеку!")
        await state.set_state(FinanceStates.wait_receipt)
    except: await m.answer("Введіть число!")

@dp.message(FinanceStates.wait_receipt, F.photo)
async def dep_r(m: types.Message, state: FSMContext):
    d = await state.get_data()
    tid = str(uuid.uuid4())[:8]
    await finance_col.insert_one({"_id": tid, "uid": m.from_user.id, "amt": d['amt'], "type": "dep"})
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Схвалити", callback_data=f"fok_{tid}"), InlineKeyboardButton(text="❌ Відхилити", callback_data=f"fno_{tid}")]])
    await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=f"📥 Поповнення {d['amt']} від {m.from_user.id}", reply_markup=kb)
    await m.answer("✅ Чек надіслано адміністрації!", reply_markup=main_kb())
    await state.clear()

@dp.callback_query(F.data == "draw")
async def draw_s(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(FinanceStates.wait_draw_sum)
    await cb.message.answer("💎 Сума для виводу (мін. 4.50):", reply_markup=cancel_kb())

@dp.message(FinanceStates.wait_draw_sum)
async def draw_1(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text)
        u = await get_u(m.from_user.id)
        if amt < 4.50 or u['balance'] < amt: return await m.answer("❌ Менше ліміту або недостатньо коштів!")
        await state.update_data(amt=amt)
        await m.answer("Введіть ваш IBAN:")
        await state.set_state(FinanceStates.wait_draw_iban)
    except: await m.answer("Введіть число!")

@dp.message(FinanceStates.wait_draw_iban)
async def draw_2(m: types.Message, state: FSMContext):
    await state.update_data(iban=m.text)
    await m.answer("Введіть ПІБ:")
    await state.set_state(FinanceStates.wait_draw_pib)

@dp.message(FinanceStates.wait_draw_pib)
async def draw_3(m: types.Message, state: FSMContext):
    await state.update_data(pib=m.text)
    await m.answer("Введіть ІПН:")
    await state.set_state(FinanceStates.wait_draw_ipn)

@dp.message(FinanceStates.wait_draw_ipn)
async def draw_4(m: types.Message, state: FSMContext):
    d = await state.get_data()
    tid = str(uuid.uuid4())[:8]
    await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -d['amt']}})
    await finance_col.insert_one({"_id": tid, "uid": m.from_user.id, "amt": d['amt'], "type": "draw", "info": f"IBAN: {d['iban']}\nПІБ: {d['pib']}\nІПН: {m.text}"})
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Виплачено", callback_data=f"dok_{tid}"), InlineKeyboardButton(text="❌ Відхилити", callback_data=f"dno_{tid}")]])
    await bot.send_message(ADMIN_ID, f"📤 ЗАЯВКА НА ВИВІД {d['amt']} 💎\n{d['iban']}\n{d['pib']}\nІПН: {m.text}", reply_markup=kb)
    await m.answer("✅ Заявку створено!", reply_markup=main_kb())
    await state.clear()

# --- АДМІН-ФУНКЦІЇ ---
@dp.callback_query(F.data.startswith("fok_"))
async def adm_fok(cb: types.CallbackQuery):
    tid = cb.data[4:]; f = await finance_col.find_one({"_id": tid})
    if f:
        await users_col.update_one({"_id": f['uid']}, {"$inc": {"balance": f['amt']}})
        await bot.send_message(f['uid'], f"✅ Нараховано {f['amt']} 💎")
        await cb.message.delete(); await finance_col.delete_one({"_id": tid})

@dp.callback_query(F.data.startswith("fno_"))
async def adm_fno(cb: types.CallbackQuery):
    tid = cb.data[4:]; f = await finance_col.find_one({"_id": tid})
    if f:
        await bot.send_message(f['uid'], "❌ Поповнення відхилено.")
        await cb.message.delete(); await finance_col.delete_one({"_id": tid})

@dp.callback_query(F.data.startswith("dok_"))
async def adm_dok(cb: types.CallbackQuery):
    tid = cb.data[4:]; await cb.message.answer("Виплачено!"); await cb.message.delete()
    await finance_col.delete_one({"_id": tid})

@dp.callback_query(F.data.startswith("dno_"))
async def adm_dno(cb: types.CallbackQuery):
    tid = cb.data[4:]; f = await finance_col.find_one({"_id": tid})
    if f:
        await users_col.update_one({"_id": f['uid']}, {"$inc": {"balance": f['amt']}})
        await bot.send_message(f['uid'], "❌ Вивід відхилено. Кошти повернуто.")
        await cb.message.delete(); await finance_col.delete_one({"_id": tid})

@dp.message(F.text == "⚙️ Налаштування")
async def sett_cmd(m: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🆘 Support", url="https://t.me/vex0o0")]])
    if m.from_user.id == ADMIN_ID: kb.inline_keyboard.append([InlineKeyboardButton(text="👨‍💻 Admin", callback_data="admin")])
    await m.answer("⚙️ Налаштування:", reply_markup=kb)

@dp.callback_query(F.data == "admin")
async def adm_p(cb: types.CallbackQuery):
    c = await users_col.count_documents({})
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏆 Топ Рефералів", callback_data="adm_top_ref")],
        [InlineKeyboardButton(text="📊 Топ Оборотів", callback_data="adm_top_turn")]
    ])
    await cb.message.answer(f"📊 Статистика:\nВсього юзерів: {c}", reply_markup=kb)

@dp.callback_query(F.data == "adm_top_ref")
async def adm_top_ref(cb: types.CallbackQuery):
    top = await users_col.find().sort("referals_count", -1).limit(10).to_list(10)
    text = "🏆 **Адмін: Топ 10 по рефералах:**\n\n"
    for i, u in enumerate(top, 1): text += f"{i}. ID: `{u['_id']}` — {u.get('referals_count', 0)}\n"
    await cb.message.answer(text, parse_mode="Markdown")

@dp.callback_query(F.data == "adm_top_turn")
async def adm_top_turn(cb: types.CallbackQuery):
    top = await users_col.find().sort("turnover", -1).limit(10).to_list(10)
    text = "📊 **Адмін: Топ 10 по обороту:**\n\n"
    for i, u in enumerate(top, 1): text += f"{i}. ID: `{u['_id']}` — {u.get('turnover', 0.0):.2f}\n"
    await cb.message.answer(text, parse_mode="Markdown")

async def main(): await dp.start_polling(bot)
if __name__ == "__main__": asyncio.run(main())
